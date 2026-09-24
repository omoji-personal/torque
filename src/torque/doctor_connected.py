"""Readiness of a connected workspace: the gate hook, the host permission rules,
the approval tier, the client's consent and org identity, and synthetic probes
run through the configured hook. Local checks; org identity only with --live."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from . import consent, permissions, workspace as ws

PROBE_CLIENT = "doctor-probe"
PROBES = (
    ("org_write", "Bash", {"command": "sf project deploy start -m Flow:Doctor_Probe -o doctor-probe"}, "deny"),
    ("read_unbound", "Bash", {"command": "sf data query -q 'SELECT Id FROM Organization' -o doctor-probe"}, "deny"),
    ("unverifiable", "Bash", {"command": "python3 doctor_probe.py"}, "ask"),
    ("admin", "Bash", {"command": "torque approval grant req-000000000000 --workspace . --client doctor-probe"},
     "deny"),
    ("browser_write", "mcp__claude-in-chrome__computer", {"action": "left_click"}, "deny"),
)


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
    the host would, with a client binding no workspace has."""
    from .cli import _hook_shell

    def probe(event: dict) -> tuple[int | None, str]:
        env = {**os.environ, "TORQUE_CLIENT": PROBE_CLIENT}
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


def _settings(root: Path) -> dict:
    merged: dict = {"permissions": {}}
    for name in ("settings.json", "settings.local.json"):
        try:
            data = json.loads((root / ".claude" / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        perms = data.get("permissions") if isinstance(data, dict) else None
        if not isinstance(perms, dict):
            continue
        for key, value in perms.items():
            if isinstance(value, list):
                merged["permissions"][key] = [*merged["permissions"].get(key, []), *value]
            elif key not in merged["permissions"] or name == "settings.json":
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
    for entry in (env.get("PATH") or "").split(os.pathsep):
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


def report(root: Path, client: str | None, live: bool = False, resolve=None, probe=None) -> dict:
    from .cli import HOOK_TIMEOUT, _gate_hook_report
    root = Path(root)
    _, config = ws.load_workspace(root)
    problems: list[str] = []
    advice: list[str] = []
    hook = _gate_hook_report(root)["hook"]
    if not hook["configured"]:
        problems.append("no torque.gate hook is wired in .claude/settings.json; add it as docs/ai-access.md shows")
    else:
        if not hook["isolated"]:
            problems.append("the torque.gate hook runs Python without -I; use: " + hook["recommended_command"])
        if not hook["matcher_covers_tools"]:
            problems.append('the torque.gate hook matcher must be ".*"')
        if any(t != HOOK_TIMEOUT for t in hook["timeouts"]):
            problems.append(f'set "timeout": {HOOK_TIMEOUT} on the torque.gate hook entry')
        if hook["disabled_by"]:
            problems.append("hooks are disabled by " + "; ".join(hook["disabled_by"]))
    drift = permissions.drift(_settings(root), permissions.generate())
    if drift:
        problems.append(f"the host permission rules are not in place ({len(drift)} issues, first: {drift[0]}); "
                        "run torque approval permissions --workspace W --write yourself")
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
    for route, tool, tool_input, expected in PROBES:
        event = {"hook_event_name": "PreToolUse", "cwd": str(root), "session_id": "doctor-probe",
                 "tool_use_id": f"doctor-{route}", "permission_mode": "default", "tool_name": tool,
                 "tool_input": tool_input}
        got = _outcome(*runner(event)) if runner else "not run"
        probes.append({"route": route, "event": event, "expected": expected, "got": got})
        if got != expected:
            problems.append(f"probe {route}: expected {expected}, got {got}")
    return {"ready": not problems, "problems": problems, "advice": advice, "probes": probes,
            "approval_verify": verify, "client": client_view}


def print_report(result: dict, out=None) -> None:
    out = out or sys.stdout
    for probe in result["probes"]:
        mark = "ok" if probe["got"] == probe["expected"] else "MISMATCH"
        out.write(f"Probe {probe['route']:<14} expected {probe['expected']:<5} got {probe['got']:<5} {mark}\n")
