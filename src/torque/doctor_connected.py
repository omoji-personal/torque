"""Readiness of a connected workspace: the gate hook, the host permission rules,
the approval tier, the client's consent and org identity, and synthetic probes
run through the configured hook. Local checks; org identity only with --live.

Under the unattended permission profile it also checks that the workspace is a
tier 2 one with a named approver delegate, that the gate hook runs after each
call too (PostToolUse and PostToolUseFailure, for execution records), that the
permission sidecar still matches the settings it was written with, and that the
session cannot rewrite that sidecar; and it says what a `claude -p` session does
with each probe's answer (UNDER_P)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

from . import consent, permissions, workspace as ws
from .delegation import setup_steps

PROBE_CLIENT = "doctor-probe"
PROBES = (
    ("org_write", "Bash", {"command": "sf project deploy start -m Flow:Doctor_Probe -o doctor-probe"}, "deny"),
    ("read_unbound", "Bash", {"command": "sf data query -q 'SELECT Id FROM Organization' -o doctor-probe"}, "deny"),
    # Refused because no client is bound; the gate logs a check-only call only after allowing it.
    ("check_only_unbound", "Bash", {"command": "sf project deploy validate -m Flow:Doctor_Probe -o doctor-probe"},
     "deny"),
    ("unverifiable", "Bash", {"command": "python3 doctor_probe.py"}, "ask"),
    ("admin", "Bash", {"command": "torque approval grant req-000000000000 --workspace . --client doctor-probe"},
     "deny"),
    ("browser_write", "mcp__claude-in-chrome__computer", {"action": "left_click"}, "deny"),
)
# The 13 probe routes, unbound then bound (the spec says 12; a15 has 13, F44).
PROBE_ROUTES = tuple(route for route, *_ in PROBES) + (
    "bound_read", "bound_write_unapproved", "bound_org_outside_consent", "bound_default_org", "bound_other_client",
    "bound_unverifiable", "bound_skipped_prompts")
REFUSED = "refused: the hook denies it, no prompt"
# What a `claude -p` session does with the gate's answer to each probe under the
# unattended profile: a deny is final, and an ask has no one to answer it.
UNDER_P = {**{route: REFUSED for route in PROBE_ROUTES},
           "unverifiable": "refused: the ask has no one to answer under claude -p",
           "bound_unverifiable": "refused: the ask has no one to answer under claude -p",
           "bound_read": "runs only if a read allow rule covers it; otherwise refused under claude -p"}
HOOK_EVENTS = ("PreToolUse", "PostToolUse", "PostToolUseFailure")
POST_EVENTS = HOOK_EVENTS[1:]
_GATED_DRIFT = "the unattended profile must not ask on "
_INTERPRETER_RE = re.compile(r'^\s*(?:"([^"]*)"|(\S+))')


def _outcome(code: int | None, stdout: str) -> str:
    if code == 2:
        return "deny"
    if code == 0:
        try:
            data = json.loads(stdout or "{}")
            decision = data.get("hookSpecificOutput", {}).get("permissionDecision")
        except (ValueError, AttributeError):
            decision = None
        return "ask" if decision == "ask" else "deny" if decision == "deny" else "allow"
    return f"error (exit {code})"


def _hook_probe(root: Path, commands: list[str]):
    """A probe that runs each configured hook command on one synthetic event, as
    the host would. Unbound, with a client binding no workspace has and no launch
    record; bound (`_torque_client` and `_torque_launch` in the event), with the
    doctor's own probe launch record, so the hook binds through a real record
    (requirement 9) and not through TORQUE_CLIENT alone."""
    from .cli import _hook_shell

    def probe(event: dict) -> tuple[int | None, str]:
        event = dict(event)
        env = {**os.environ, "TORQUE_CLIENT": event.pop("_torque_client", PROBE_CLIENT)}
        launch_id = event.pop("_torque_launch", None)
        env.pop("TORQUE_LAUNCH", None)
        if launch_id:
            env["TORQUE_LAUNCH"] = launch_id
        env.pop("CLAUDE_PROJECT_DIR", None)
        worst = (0, "")
        for command in commands:
            shell = _hook_shell()
            try:
                if shell:
                    run = subprocess.run([shell, "-c", command], cwd=root, input=json.dumps(event), env=env,
                                         capture_output=True, text=True, timeout=60)
                else:
                    run = subprocess.run(command, shell=True, cwd=root, input=json.dumps(event), env=env,
                                         capture_output=True, text=True, timeout=60)
            except (OSError, subprocess.TimeoutExpired):
                return None, ""
            if run.returncode != 0:
                return run.returncode, run.stdout
            if "permissionDecision" in run.stdout:
                worst = (0, run.stdout)
        return worst
    return probe


def _user_settings_path() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(base) / "settings.json" if base else Path.home() / ".claude" / "settings.json"


def _settings(root: Path) -> dict:
    """The effective permissions across the user, project and local settings files:
    lists are combined; for a single value the local file wins over the project file,
    which wins over the user file (managed settings, if any, are outside this check)."""
    merged: dict = {"permissions": {}}
    layers = [_user_settings_path(), root / ".claude" / "settings.json",
              root / ".claude" / "settings.local.json"]
    for path in layers:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        perms = data.get("permissions") if isinstance(data, dict) else None
        if not isinstance(perms, dict):
            continue
        for key, value in perms.items():
            if isinstance(value, list):
                merged["permissions"][key] = [*merged["permissions"].get(key, []), *value]
            else:
                merged["permissions"][key] = value
    return merged


def _sf_shadowing(env=os.environ) -> list[str]:
    """Advice when a program named sf could be placed ahead of the real one, or
    the Salesforce CLI's own files are writable by this account."""
    notes = []
    real = shutil.which("sf", path=env.get("PATH"))
    if not real:
        return notes
    real_dir = os.path.realpath(os.path.dirname(real))
    for entry in dict.fromkeys((env.get("PATH") or "").split(os.pathsep)):
        if not entry:
            continue
        if os.path.realpath(entry) == real_dir:
            break
        if os.path.isdir(entry) and os.access(entry, os.W_OK):
            notes.append(f"{entry} comes before the Salesforce CLI on PATH and this account can write it; "
                         "a program named sf placed there would run in place of the approved one")
    install = Path(os.path.realpath(real)).parent.parent
    if os.access(install, os.W_OK):
        notes.append(f"the Salesforce CLI's installation ({install}) is writable by this account, so code the "
                     "session writes there would run under an approved sf command; install it for another "
                     "account (or use tier 2 with a separate agent account that cannot write it)")
    return notes


# setup_steps (F25) lives in delegation.py, imported above: approval.py's log
# (task D16) reads it too, and a lower-level module (the approval store)
# should not import this higher-level one (the doctor), even lazily.


def approvals_by_kind(root: Path, client: str | None = None) -> dict:
    """Grants in the approval log by approver kind (F19): for `client`, or every
    client when none is given. A grant recorded without a kind (made before a16)
    is counted as "unrecorded"."""
    from . import approval
    counts = {"human": 0, "ai": 0}
    if client:
        names = [client]
    else:
        clients = root / "clients"
        names = sorted(p.name for p in clients.iterdir() if (p / "client.json").is_file()) if clients.is_dir() else []
    for name in names:
        for row in approval.approval_log(root, name):
            if row.get("kind") != "approval_grant":
                continue
            kind = row.get("approver_kind")
            key = kind if kind in ("human", "ai") else "unrecorded"
            counts[key] = counts.get(key, 0) + 1
    return counts


def approval_history(root: Path, client: str | None = None) -> dict:
    """V2 M1 (requirement 19): who has approved and launched, from the approval
    log: verified launch records grouped by actor (account, uid, kind, model),
    the count of launches that are not verified, and each approval identity
    with its grant and denial counts. For `client`, or every client."""
    from . import approval
    if client:
        names = [client]
    else:
        clients = root / "clients"
        names = sorted(p.name for p in clients.iterdir() if (p / "client.json").is_file()) if clients.is_dir() else []
    launches: dict[tuple, int] = {}
    unverified = 0
    identities: dict[tuple, dict] = {}
    for name in names:
        for row in approval.approval_log(root, name):
            who = (row.get("approver"), row.get("approver_uid"), row.get("approver_kind"), row.get("approver_model"))
            if row.get("kind") == "launch":
                if row.get("verified") is True:
                    launches[who] = launches.get(who, 0) + 1
                else:
                    unverified += 1
            elif row.get("kind") in ("approval_grant", "approval_deny"):
                key = (*who, bool(row.get("delegated")))
                item = identities.setdefault(key, {"grants": 0, "denials": 0})
                item["grants" if row["kind"] == "approval_grant" else "denials"] += 1
    order = lambda item: tuple(str(v) for v in item[0])
    return {"launches": {"verified": [{"actor": a, "uid": u, "kind": k, "model": m, "count": n}
                                      for (a, u, k, m), n in sorted(launches.items(), key=order)],
                         "unverified": unverified},
            "identities": [{"actor": a, "uid": u, "kind": k, "model": m, "delegated": d, **counts}
                           for (a, u, k, m, d), counts in sorted(identities.items(), key=order)]}


def _who(item: dict) -> str:
    model = f", model {item['model']}" if item.get("model") else ""
    return f"{item.get('actor')} (uid {item.get('uid')}, {item.get('kind')}{model}"


def _hook_entries(root: Path) -> dict[str, list[tuple[str, str]]]:
    """(matcher, command) for each torque.gate hook, per event, from the workspace's
    settings.json and settings.local.json."""
    found: dict[str, list[tuple[str, str]]] = {event: [] for event in HOOK_EVENTS}
    for name in ("settings.json", "settings.local.json"):
        try:
            data = json.loads((root / ".claude" / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        hooks = data.get("hooks") if isinstance(data, dict) else None
        if not isinstance(hooks, dict):
            continue
        for event in HOOK_EVENTS:
            entries = hooks.get(event)
            for entry in entries if isinstance(entries, list) else []:
                if not isinstance(entry, dict):
                    continue
                matcher = str(entry.get("matcher") or "")
                for item in entry.get("hooks") or []:
                    command = str(item.get("command") or "") if isinstance(item, dict) else ""
                    if "torque.gate" in command:
                        found[event].append((matcher, command))
    return found


def _interpreter(command: str) -> str | None:
    match = _INTERPRETER_RE.match(command)
    return (match.group(1) or match.group(2)) if match else None


def rewrite_command(root: Path, profile: str, sidecar: dict | None, entries: dict,
                    hook_python: str | None = None) -> str:
    """The exact command that rewrites this workspace's rules for its CURRENT profile
    (fix round 1): `approval permissions --write` defaults to interactive, so an
    instruction without --unattended would silently downgrade an unattended workspace.
    --with-hooks when the gate hook is wired, and the recorded (or given) interpreter."""
    words = ["torque", "approval", "permissions", "--workspace", str(root), "--write"]
    if profile == "unattended":
        words.append("--unattended")
    if any(entries[event] for event in HOOK_EVENTS) or profile == "unattended":
        words.append("--with-hooks")
        recorded = (sidecar or {}).get("hook_python")
        python = hook_python or (recorded if isinstance(recorded, str) and recorded else None)
        if python:
            words += ["--hook-python", python]
    text = " ".join(word if word == "PYTHON" else shlex.quote(word) for word in words)
    if profile == "invalid":
        text += " (add --unattended if this workspace ran the unattended profile)"
    return text


def _interpreter_problem(command: str, fix: str) -> str:
    """Why the interpreter a hook command starts cannot run here, or "". Claude Code
    treats a hook that cannot start as a non-blocking error, so the call goes ahead
    with no gate at all: the hook fails open."""
    python = _interpreter(command)
    if not python:
        return "the torque.gate hook command names no interpreter; the host would run no gate (the hook fails open)"
    path = python if os.path.isabs(python) or "/" in python else shutil.which(python)
    if not path or not os.path.isfile(path) or not os.access(path, os.X_OK):
        return (f"the torque.gate hook's interpreter {python} is missing or not executable by this account, so the "
                f"host would run no gate for any call (the hook fails open); rewrite the rules with: {fix} (PYTHON "
                "is an interpreter the agent account can run)")
    return ""


def _post_hook_problems(entries: dict, pre_commands: list[str], profile: str, fix: str) -> list[str]:
    """F3: the after-call hooks run the same command as PreToolUse, for every tool
    (matcher ".*"). The unattended profile needs both, for execution records."""
    from .cli import _matcher_covers
    problems = []
    for event in POST_EVENTS:
        found = entries[event]
        if not found:
            if profile == "unattended":
                problems.append(f"the unattended profile needs the torque.gate hook under {event} too, for "
                                f"execution records; rewrite the rules with: {fix}")
            continue
        if not _matcher_covers([matcher for matcher, _ in found]):
            problems.append(f'the torque.gate hook under {event} must use the matcher ".*", so every tool call '
                            "is recorded")
        if pre_commands and any(command not in pre_commands for _, command in found):
            problems.append(f"the torque.gate hook under {event} must run the same command as under PreToolUse")
    return problems


def _sidecar_problems(root: Path, sidecar: dict | None, profile: str, entries: dict, fix: str) -> list[str]:
    """Drift between the permission sidecar and the settings it was written with."""
    if sidecar is None:
        return []
    if profile == "invalid":
        return [f"the permission sidecar .claude/torque-permissions.json is unreadable; rewrite the rules with: {fix}"]
    problems = []
    try:
        actual = hashlib.sha256((root / ".claude" / "settings.json").read_bytes()).hexdigest()
    except OSError:
        actual = None
    recorded = sidecar.get("settings_sha256")
    if recorded != actual:
        now = f"hashes to {actual}" if actual else "cannot be read"
        problems.append(f"the permission sidecar {permissions.PROFILE_FILE} records settings_sha256 "
                        f"{recorded or 'none'}, but .claude/settings.json {now}: the rules changed after they were "
                        f"written, or the sidecar was edited; rewrite the rules with: {fix}")
    hook_python = sidecar.get("hook_python")
    if isinstance(hook_python, str) and hook_python:
        seen = set()
        for event in HOOK_EVENTS:
            for _, command in entries[event]:
                python = _interpreter(command)
                if python != hook_python and (event, python) not in seen:
                    seen.add((event, python))
                    problems.append(f"the torque.gate hook under {event} runs {python}, but the rules were written "
                                    f"for {hook_python}; rewrite the rules with: {fix}")
    return problems


def _post_outcome(code: int | None, stdout: str) -> str:
    return "ran" if code == 0 else f"error (exit {code})"


def report(root: Path, client: str | None, live: bool = False, resolve=None, probe=None) -> dict:
    from . import delegation, gate_connected, launch
    from .cli import HOOK_TIMEOUT, _gate_hook_report
    root = Path(root)
    _, config = ws.load_workspace(root)
    problems: list[str] = []
    advice: list[str] = []
    profile = permissions.load_profile(root)
    sidecar = permissions.load_sidecar(root)
    hook = _gate_hook_report(root)["hook"]
    entries = _hook_entries(root)
    if not hook["configured"]:
        problems.append("no torque.gate hook is wired in .claude/settings.json; add it as docs/ai-access.md shows")
    else:
        if not hook["fail_closed_shim"]:
            problems.append("the torque.gate hook is not the fail-closed form (it would let calls through if "
                            "Torque failed to load); use: " + hook["recommended_command"])
        if not hook["isolated"]:
            problems.append("the torque.gate hook runs Python without -I; use: " + hook["recommended_command"])
        if not hook["matcher_covers_tools"]:
            problems.append('the torque.gate hook matcher must be ".*"')
        if any(t != HOOK_TIMEOUT for t in hook["timeouts"]):
            problems.append(f'set "timeout": {HOOK_TIMEOUT} on the torque.gate hook entry')
        if hook["disabled_by"]:
            problems.append("hooks are disabled by " + "; ".join(hook["disabled_by"]))
    # R43: the hook runs the interpreter written in settings; one this account cannot run fails open.
    commands = list(dict.fromkeys(command for event in HOOK_EVENTS for _, command in entries[event]))
    fix = rewrite_command(root, profile, sidecar, entries)
    replace_python = rewrite_command(root, profile, sidecar, entries, hook_python="PYTHON")
    problems += list(dict.fromkeys(p for p in (_interpreter_problem(c, replace_python) for c in commands) if p))
    problems += _post_hook_problems(entries, hook["commands"], profile, fix)
    checked = profile if profile in permissions.PROFILES else "interactive"
    drift = permissions.drift(_settings(root), permissions.generate(checked), checked)
    gated = [item for item in drift if item.startswith(_GATED_DRIFT)]
    drift = [item for item in drift if item not in gated]
    if drift:
        problems.append(f"the host permission rules are not in place ({len(drift)} issues, first: {drift[0]}); "
                        f"run {fix} yourself")
    problems += gated
    problems += _sidecar_problems(root, sidecar, profile, entries, fix)
    if profile == "unattended":
        if not gate_connected.unattended(root):
            problems.append("the unattended profile is only for a tier 2 workspace whose approver is a named "
                            "delegate")
        if hasattr(os, "getuid"):
            advice.append(f"the probes ran as uid {os.getuid()}; run doctor as the agent account the session "
                          "uses, so they prove that account can run the hook")
    verify = config.get("approval_verify", "hmac")
    if verify == "hmac":
        from .approval import key_path
        key = key_path()
        if key.exists() and os.name != "nt" and (key.stat().st_mode & 0o077 or key.is_symlink()):
            problems.append(f"{key} must be a regular file readable by its owner only (chmod 600)")
        advice.append("approvals use tier 1 (a key in this account's home): a script the session runs can read "
                      "it and forge an approval. Tier 2 (a separate approver account) is recommended.")
    elif verify == "owner-uid":
        if type(config.get("approver_uid")) is not int:
            problems.append("owner-uid verification needs approver_uid in workspace.json")
        elif hasattr(os, "getuid") and config["approver_uid"] == os.getuid():
            problems.append("approver_uid is this account; the approver must be a separate OS account")
    else:
        problems.append(f"unknown approval_verify {verify!r}")
    advice += _sf_shadowing()
    client_view = None
    if client:
        item = consent.load_consent(root, client)
        issues = consent.consent_problems(item)
        problems += [f"{client}: {issue}" for issue in issues]
        client_view = {"consent": item.get("status") if item else None, "orgs": []}
        if live and item and not issues:
            if resolve is None:
                from jsc_revert.org_detect import resolve_org as resolve
            for org in item["approved_orgs"]:
                info = resolve(org["alias"])
                live_id = getattr(info, "org_id_18", None)
                client_view["orgs"].append({"alias": org["alias"], "recorded": org["org_id_18"], "live": live_id})
                if live_id != org["org_id_18"]:
                    problems.append(f"{org['alias']} resolves to {live_id or 'nothing'}, but the consent records "
                                    f"{org['org_id_18']}")
    probes = []
    runner = probe or (_hook_probe(root, hook["commands"]) if hook["commands"] else None)
    cases = [(route, tool, tool_input, expected, None) for route, tool, tool_input, expected in PROBES]
    slug = None
    if client and client_view is not None and not consent.consent_problems(consent.load_consent(root, client)):
        # Bound probes, as a session launched for this client would see them. None reaches an org:
        # the hook decides and exits; nothing it allows is run.
        slug = ws.slug_for(client)
        alias = consent.load_consent(root, client)["approved_orgs"][0]["alias"]
        cases += [
            ("bound_read", "Bash", {"command": f"sf org display -o {alias}"}, "allow", slug),
            ("bound_write_unapproved", "Bash",
             {"command": f"sf project deploy start -m Flow:Doctor_Probe -o {alias}"}, "deny", slug),
            ("bound_org_outside_consent", "Bash", {"command": "sf org display -o doctor-probe-other"}, "deny", slug),
            ("bound_default_org", "Bash", {"command": "sf org display"}, "deny", slug),
            ("bound_other_client", "Bash", {"command": "torque context --workspace . --client doctor-probe-other"},
             "deny", slug),
            ("bound_unverifiable", "Bash", {"command": "python3 doctor_probe.py"}, "ask", slug),
            ("bound_skipped_prompts", "Bash", {"command": "python3 doctor_probe.py"}, "deny", slug),
        ]
    # The session must not be able to rewrite the permission sidecar (and with it its own
    # profile): the gate denies file-tool and Bash writes to the workspace's .claude/ (R51).
    sidecar_path = str(root / permissions.PROFILE_FILE)
    checks_cases = [
        ("sidecar_write", "PreToolUse", "Write", {"file_path": sidecar_path, "content": "{}"}, "deny", {}),
        ("sidecar_bash_write", "PreToolUse", "Bash",
         {"command": f"echo '{{}}' > {permissions.PROFILE_FILE}"}, "deny", {}),
    ]
    # The after-call hooks, for any tool: a browser (MCP) call and a failed Bash call. They record
    # only approved calls, so these synthetic ones write nothing; they prove the hook starts.
    if entries["PostToolUse"]:
        checks_cases.append(("post_hook_mcp", "PostToolUse", "mcp__claude-in-chrome__computer",
                             {"action": "screenshot"}, "ran", {"tool_response": {}}))
    if entries["PostToolUseFailure"]:
        checks_cases.append(("post_hook_failure", "PostToolUseFailure", "Bash", {"command": "python3 doctor_probe.py"},
                             "ran", {"error": "Exit code 1", "is_interrupt": False}))
    checks = []
    record_id = None
    try:
        if slug:
            # F49: bound probes bind through a real launch record, a `probe` one for this
            # process, removed afterwards (the file only; F1 created any missing folder).
            try:
                record_id = launch.write_launch_record(root, client, "probe")["id"]
            except (OSError, ws.WorkspaceError) as exc:
                problems.append(f"could not write the doctor's probe launch record, so the bound probes cannot "
                                f"bind: {exc}")
        for route, tool, tool_input, expected, bound in cases:
            event = {"hook_event_name": "PreToolUse", "cwd": str(root), "session_id": "doctor-probe",
                     "tool_use_id": f"doctor-{route}",
                     "permission_mode": "bypassPermissions" if route == "bound_skipped_prompts" else "default",
                     "tool_name": tool, "tool_input": tool_input}
            if bound:
                event["_torque_client"] = bound
                if record_id:
                    event["_torque_launch"] = record_id
            got = _outcome(*runner(event)) if runner else "not run"
            event.pop("_torque_client", None)
            event.pop("_torque_launch", None)
            probes.append({"route": route, "event": event, "expected": expected, "got": got, "bound": bound,
                           "under_p": UNDER_P[route] if profile == "unattended" else None})
            if got != expected:
                problems.append(f"probe {route}: expected {expected}, got {got}")
        for name, event_name, tool, tool_input, expected, extra in checks_cases:
            event = {"hook_event_name": event_name, "cwd": str(root), "session_id": "doctor-probe",
                     "tool_use_id": f"doctor-{name}", "permission_mode": "default", "tool_name": tool,
                     "tool_input": tool_input, **extra}
            if event_name == "PreToolUse":
                run, judge = runner, _outcome
            else:
                run = probe or _hook_probe(root, list(dict.fromkeys(c for _, c in entries[event_name])))
                judge = _post_outcome
            if slug:
                event["_torque_client"] = slug
                if record_id:
                    event["_torque_launch"] = record_id
            got = judge(*run(event)) if run else "not run"
            event.pop("_torque_client", None)
            event.pop("_torque_launch", None)
            checks.append({"check": name, "event": event, "expected": expected, "got": got, "ok": got == expected,
                           "bound": slug})
            if got != expected:
                why = (" (the session could rewrite the permission sidecar and change its own profile)"
                       if name.startswith("sidecar") else " (the after-call hook does not run cleanly)")
                problems.append(f"check {name}: expected {expected}, got {got}{why}")
    finally:
        if record_id:
            try:
                from . import approval
                (approval._dirs(root, client, create=False)["consumed"] / f"{record_id}.launch").unlink()
            except (OSError, ws.WorkspaceError):
                advice.append(f"remove the doctor's probe launch record {record_id}.launch from the client's "
                              "approvals/consumed folder")
    try:
        by_kind = approvals_by_kind(root, client if client_view is not None else None)
    except (OSError, ValueError, KeyError, ws.WorkspaceError) as exc:
        by_kind = None
        advice.append(f"approvals by kind could not be counted: {exc}")
    try:
        history = approval_history(root, client if client_view is not None else None)
    except (OSError, ValueError, KeyError, ws.WorkspaceError, delegation.Refusal) as exc:
        history = None
        advice.append(f"launches and approval identities could not be read from the approval log: {exc}")
    return {"ready": not problems, "problems": problems, "advice": advice, "probes": probes, "checks": checks,
            "approval_verify": verify, "client": client_view, "profile": profile,
            "delegates": {role: delegation.delegate_for(config, role) for role in delegation.ROLES},
            "setup_steps": setup_steps(root), "approvals_by_kind": by_kind, "approval_history": history}


def print_report(result: dict, out=None) -> None:
    out = out or sys.stdout
    profile = result.get("profile")
    if profile:
        out.write(f"Permission profile: {profile}\n")
    delegates = result.get("delegates") or {}
    if profile == "unattended" or any(delegates.values()):
        for role, label in (("approver", "Approver"), ("setup", "Setup delegate")):
            item = delegates.get(role)
            out.write(f"{label}: {item['account']} (uid {item['uid']}, {item['kind']})\n" if item
                      else f"{label}: none named\n")
    for step in result.get("setup_steps") or []:
        model = f", model {step['model']}" if step.get("model") else ""
        out.write(f"Setup step {step['step']}: {step.get('kind')} {step.get('account')} (uid {step.get('uid')}"
                  f"{model}) via {step.get('via')} at {step.get('at')}\n")
    counts = result.get("approvals_by_kind")
    if counts is not None:
        out.write("Approvals by kind: " + ", ".join(f"{kind} {n}" for kind, n in counts.items()) + "\n")
    history = result.get("approval_history")
    if history is not None:
        for item in history["launches"]["verified"]:
            out.write(f"Verified launch: {_who(item)}): {item['count']}\n")
        if history["launches"]["unverified"]:
            out.write(f"Unverified launches: {history['launches']['unverified']}\n")
        for item in history["identities"]:
            kind = ", delegated" if item["delegated"] else ""
            out.write(f"Approval identity: {_who(item)}{kind}): {item['grants']} granted, "
                      f"{item['denials']} denied\n")
    for probe in result["probes"]:
        mark = "ok" if probe["got"] == probe["expected"] else "MISMATCH"
        line = f"Probe {probe['route']:<25} expected {probe['expected']:<5} got {probe['got']:<5} {mark}"
        if probe.get("under_p"):
            line += f"; under claude -p: {probe['under_p']}"
        out.write(line + "\n")
    for check in result.get("checks") or []:
        mark = "ok" if check["ok"] else "MISMATCH"
        out.write(f"Check {check['check']:<25} expected {check['expected']:<5} got {check['got']:<5} {mark}\n")
