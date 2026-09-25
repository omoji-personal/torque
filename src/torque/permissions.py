"""Claude Code permission rules for a connected workspace: ask before every
Salesforce and Torque write route, every interpreter and runner and every browser
server; deny approval administration and edits to mode, consent, approval and
Salesforce CLI files; never allow bypass mode. The host enforces these rules; the
gate enforces its own checks as well (docs/connected-approval.md)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

from . import workspace as ws

SF_WRITE_PREFIXES = (
    "sf project deploy start", "sf project deploy quick", "sf project deploy resume", "sf project delete",
    "sf project reset", "sf data create", "sf data update", "sf data upsert", "sf data delete",
    "sf data import", "sf apex run", "sf org assign", "sf org create", "sf org delete", "sf org refresh",
    "sf package install", "sf package uninstall", "sf api request", "sf community publish", "sf agent",
    "sfdx",
)
TORQUE_WRITE_PREFIXES = ("torque deploy", "torque data", "torque org", "torque recover", "torque revert",
                         "torque browser", "torque qa", "torque probes", "torque ai-regression", "jsc")
INTERPRETERS = ("python", "python3", "py", "node", "deno", "bun", "ruby", "perl", "php", "bash", "sh", "zsh",
                "pwsh", "osascript", "npx", "npm", "pnpm", "yarn", "make", "curl", "wget", "xargs", "eval",
                "source", "claude")
# Interpreter and runner families, any version or variant (python3.12, nodejs, pip3).
INTERPRETER_FAMILIES = ("python", "py", "pypy", "node", "deno", "bun", "ruby", "perl", "php", "pwsh",
                        "powershell", "pip", "pipx", "uv", "npx", "npm", "pnpm", "yarn", "make", "gmake",
                        "bash", "zsh", "dash", "ksh", "fish", "osascript", "expect", "tmux", "screen", "script",
                        "sudo", "ssh", "docker", "podman", "claude", "cci", "cumulusci")
BROWSER_SERVERS = ("mcp__claude-in-chrome", "mcp__chrome-devtools", "mcp__playwright", "mcp__computer-use")
ASK_RULES = tuple([f"Bash({p}:*)" for p in (*SF_WRITE_PREFIXES, *TORQUE_WRITE_PREFIXES, *INTERPRETERS)]
                  + [f"Bash({family}*)" for family in INTERPRETER_FAMILIES]
                  + ["Bash(sh *)", "Bash(env *)", "Bash(exec *)"] + list(BROWSER_SERVERS))
FIXED_DENY_RULES = (
    "Bash(torque approval grant:*)", "Bash(torque approval deny:*)",
    "Bash(torque approval permissions *--write*)",
    "Bash(torque client consent record:*)", "Bash(torque client consent sign-off:*)",
    "Bash(torque client consent suspend:*)", "Bash(torque launch:*)", "Bash(torque workspace ai-access:*)",
    "Bash(torque workspace delegate:*)",
    "Bash(sf alias set:*)", "Bash(sf alias unset:*)", "Bash(sf config set:*)", "Bash(sf config unset:*)",
    "Edit(/workspace.json)", "Edit(/clients/*/consent.json)", "Edit(/clients/*/consent-evidence/**)",
    "Edit(/clients/*/approvals/**)", "Edit(/.claude/**)",
    "Read(~/.config/torque/approval.key)", "Edit(~/.config/torque/approval.key)",
    "Read(~/.sfdx/**)", "Edit(~/.sfdx/**)", "Read(~/.sf/**)", "Edit(~/.sf/**)",
    "Edit(~/.local/share/sf/**)", "Edit(~/.claude/settings*.json)",
)
BYPASS_KEY = "disableBypassPermissionsMode"
SKIPPING_MODES = ("bypassPermissions", "auto", "dontAsk")
PROFILES = ("interactive", "unattended")
PROFILE_FILE = ".claude/torque-permissions.json"
PROFILE_SCHEMA = "torque.permissions/1"
# Routes the gate itself decides (by approval); the unattended profile leaves them
# to the gate, because a matching ask rule still prompts after a hook allow and a
# `claude -p` session has no one to answer (docs/connected-approval.md, "Host
# facts verified", the unattended addendum row on a matching ask rule after allow).
GATED_ASK = tuple([f"Bash({p}:*)" for p in (*SF_WRITE_PREFIXES, *TORQUE_WRITE_PREFIXES)] + list(BROWSER_SERVERS))


def _absolute_rule_path(path: Path) -> str:
    """A path in Claude Code's absolute rule form: //path, with Windows drives as //c/..."""
    text = path.as_posix()
    match = re.match(r"^([A-Za-z]):/(.*)$", text)
    if match:
        return f"//{match.group(1).lower()}/{match.group(2)}"
    return "/" + text if text.startswith("/") else "//" + text


def deny_rules() -> tuple[str, ...]:
    """The fixed rules plus the approval key at its real location on this platform."""
    from .approval import key_path
    key = _absolute_rule_path(key_path())
    return FIXED_DENY_RULES + (f"Read({key})", f"Edit({key})")


DENY_RULES = FIXED_DENY_RULES


def generate(profile: str = "interactive") -> dict:
    """The `permissions` object for WORKSPACE/.claude/settings.json. Adds no allow
    rule. The unattended profile drops every ask rule for a route GATED_ASK names
    (the gate decides those by approval instead); every other ask rule, including
    the interpreter and runner backstop, stays, since a `claude -p` session has no
    one to answer an ask the gate itself does not resolve."""
    if profile not in PROFILES:
        raise ws.WorkspaceError(f"unknown permission profile {profile!r}")
    ask = [r for r in ASK_RULES if profile == "interactive" or r not in GATED_ASK]
    return {"ask": ask, "deny": list(dict.fromkeys(deny_rules())), BYPASS_KEY: "disable"}


def _body(rule: str) -> tuple[str, str] | None:
    if not rule.endswith(")") or "(" not in rule:
        return None
    tool, body = rule[:-1].split("(", 1)
    return tool, body


def _globs(body: str) -> list[str]:
    """A rule body as glob patterns over the command text (`*` is any text). A final
    `:*` means the prefix alone or followed by a space and anything."""
    if body.endswith(":*"):
        return [body[:-2], body[:-2] + " *"]
    return [body]


def _intersects(p: str, q: str) -> bool:
    """True when some text matches both glob patterns (exact, not sampled)."""
    from functools import lru_cache

    @lru_cache(maxsize=None)
    def match(i: int, j: int) -> bool:
        if i == len(p) and j == len(q):
            return True
        if i < len(p) and p[i] == "*":
            return match(i + 1, j) or (j < len(q) and match(i, j + 1))
        if j < len(q) and q[j] == "*":
            return match(i, j + 1) or (i < len(p) and match(i + 1, j))
        return i < len(p) and j < len(q) and p[i] == q[j] and match(i + 1, j + 1)
    return match(0, 0)


def _covered(rule: str) -> bool:
    """True when an allow rule could match any call the generated rules ask about or
    deny: the glob languages of the two rules intersect."""
    if rule in ASK_RULES or rule in deny_rules():
        return True
    if rule in ("Bash", "Bash(*)") or rule.startswith("mcp__*"):
        return True
    if any(rule == server or rule.startswith(server + "__") or rule.startswith(server + "*")
           for server in BROWSER_SERVERS):
        return True
    mine = _body(rule)
    if mine is None:
        return False
    tool, body = mine
    for generated in ASK_RULES + deny_rules():
        theirs = _body(generated)
        if theirs and theirs[0] == tool and any(_intersects(a, b) for a in _globs(body) for b in _globs(theirs[1])):
            return True
    return False


def merge(settings: dict, generated: dict, profile: str = "interactive") -> dict:
    """The settings with the generated rules added, allow rules for those routes
    removed, the user's other rules kept, and bypass mode disabled. Under the
    unattended profile, an existing ask rule for a GATED_ASK route is dropped too
    (F13's counterpart at merge time): a rule the owner wrote earlier, before the
    workspace switched profile, must not survive the switch to unattended."""
    out = dict(settings)
    perms = dict(out.get("permissions") or {})
    for key in ("ask", "deny"):
        existing = perms.get(key) if isinstance(perms.get(key), list) else []
        if key == "ask" and profile == "unattended":
            existing = [r for r in existing if r not in GATED_ASK]
        perms[key] = list(dict.fromkeys([*existing, *generated[key]]))
    allow = perms.get("allow") if isinstance(perms.get("allow"), list) else []
    perms["allow"] = [r for r in allow if not (isinstance(r, str) and _covered(r))]
    perms[BYPASS_KEY] = generated[BYPASS_KEY]
    if perms.get("defaultMode") in SKIPPING_MODES:
        perms.pop("defaultMode")
    out["permissions"] = perms
    return out


def _drift_a15(settings: dict, generated: dict) -> list[str]:
    """Readiness problems: missing generated rules, allow rules that overlap their
    routes (the ask still wins, but the allow shows an intent to skip it), bypass mode."""
    perms = settings.get("permissions") if isinstance(settings.get("permissions"), dict) else {}
    ask = perms.get("ask") if isinstance(perms.get("ask"), list) else []
    deny = perms.get("deny") if isinstance(perms.get("deny"), list) else []
    allow = perms.get("allow") if isinstance(perms.get("allow"), list) else []
    problems = [f"missing ask rule {r}" for r in generated["ask"] if r not in ask]
    problems += [f"missing deny rule {r}" for r in generated["deny"] if r not in deny]
    problems += [f"allows {r}, which connected mode asks about or denies" for r in allow
                 if isinstance(r, str) and _covered(r)]
    if perms.get(BYPASS_KEY) != "disable":
        problems.append("bypass permissions mode is not disabled (permissions.disableBypassPermissionsMode)")
    if perms.get("defaultMode") in SKIPPING_MODES:
        problems.append(f"defaultMode is {perms['defaultMode']}, which skips the consultant's prompts")
    return problems


def drift(settings: dict, generated: dict, profile: str = "interactive") -> list[str]:
    """`_drift_a15`'s problems, plus, under the unattended profile, any ask rule
    left on a GATED_ASK route: a matching ask rule still declines the call even
    after the gate's hook returns allow (docs/connected-approval.md), so it must
    not be there for a route the gate itself decides."""
    problems = _drift_a15(settings, generated)
    if profile == "unattended":
        perms = settings.get("permissions") if isinstance(settings.get("permissions"), dict) else {}
        ask = perms.get("ask") if isinstance(perms.get("ask"), list) else []
        problems += [f"the unattended profile must not ask on {r}; the gate decides it by approval"
                     for r in ask if r in GATED_ASK]
    return problems


def settings_path(workspace) -> Path:
    root, _ = ws.load_workspace(workspace)
    return ws._inside(root, root / ".claude" / "settings.json")


def load_sidecar(root) -> dict | None:
    """The setup sidecar's raw content, or None when it does not exist. A read
    failure (bad JSON, not an object) comes back as `{"schema": None}`, which
    `load_profile` turns into "invalid": fail closed, never treat an unreadable
    sidecar as absent-and-interactive."""
    path = Path(root) / PROFILE_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": None}
    return data if isinstance(data, dict) else {"schema": None}


def load_profile(root) -> str:
    """"interactive" (no sidecar, or a schema-mismatched empty read is treated
    the same as "no sidecar written yet"), "unattended", or "invalid" (a sidecar
    exists but is unreadable or names a profile PROFILES does not have)."""
    data = load_sidecar(root)
    if data is None:
        return "interactive"
    if data.get("schema") != PROFILE_SCHEMA or data.get("profile") not in PROFILES:
        return "invalid"
    return data["profile"]


def _with_hooks(settings: dict) -> dict:
    """Add the fail-closed gate hook under PreToolUse, PostToolUse and
    PostToolUseFailure (matcher .*), unless an entry already runs torque.gate.
    All three events are wired (F3): PostToolUse fires only for a Bash exit 0,
    and PostToolUseFailure fires instead of it for a nonzero exit
    (docs/connected-approval.md, "Host facts verified"), so recording execution
    (D14) needs both to see every call, not only the successful ones."""
    import sys
    from . import gate
    from .cli import HOOK_TIMEOUT
    command = gate.hook_command(sys.executable.replace("\\", "/"))
    out = dict(settings)
    hooks = dict(out.get("hooks") or {})
    for event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
        entries = list(hooks.get(event) or [])
        present = any("torque.gate" in str(h.get("command") or "")
                      for e in entries if isinstance(e, dict) for h in (e.get("hooks") or []) if isinstance(h, dict))
        if not present:
            entries.append({"matcher": ".*", "hooks": [{"type": "command", "command": command,
                                                        "timeout": HOOK_TIMEOUT}]})
        hooks[event] = entries
    out["hooks"] = hooks
    return out


def _owner_presence(presence, confirm) -> None:
    """The a15 check of write_settings, unchanged: presence, and the typed code
    unless a caller injects presence without a confirm."""
    injected = presence is not None
    if presence is None:
        from .presence import operator_present as presence
    check = presence()
    if not check.ok:
        raise ws.WorkspaceError(f"the owner writes permission rules at a real terminal: {check.reason}")
    if confirm is not None or not injected:
        if confirm is None:
            from .presence import confirm_code as confirm
        if not confirm():
            raise ws.WorkspaceError("the confirmation code did not match; nothing was written")


def write_settings(workspace, presence=None, confirm=None, *, profile="interactive", with_hooks=False,
                   delegated=False, model_id=None, env=None, ancestors=None, getuid=None,
                   root_owner=None) -> Path:
    """Merge the generated rules into WORKSPACE/.claude/settings.json: the owner at
    a terminal, or the workspace's setup delegate (SETUP_WRITES["permissions"]).
    The unattended profile is only for a tier 2 workspace with a named, matching
    approver delegate (delegation.delegated_tier2); a tier 1 (hmac) or
    non-connected workspace refuses with reason class tier-2-required, before any
    presence or delegated-actor check runs (an automatable refusal: no CLI flag
    can fake presence or a delegate's caller proof; see F37/the CLI test).

    F12: a file the calling delegate's own uid wrote defaults to 0600 (mkstemp),
    unreadable to the different account the gate and hooks later run as; both the
    settings file and the sidecar relax to 0644 (the containing .claude directory
    to 0755) whenever this write is delegated or the profile is unattended. The
    harness chowns them to root afterward (matches workspace.py's
    set_ai_access/_connected_rule and consent.py's _save). The owner/interactive
    path is unchanged (still mkstemp's 0600/0700).

    F13: the sidecar is also (re)written, reflecting the new profile, whenever one
    already exists, even on a plain owner/interactive write with no delegation and
    no unattended profile: otherwise an owner rewrite back to interactive would
    leave a stale "unattended" sidecar behind, and the gate (D13) would keep
    sending allow for routes this settings file now asks about again.

    `root_owner` (a callable `Path -> int`, default `path.stat().st_uid`) is
    threaded straight through to `delegation.delegated_actor`: it exists only so
    a test can name a workspace-directory owner distinct from the delegate's own
    uid (controller ruling R41, a delegate must be a separate OS account from the
    one that owns the workspace directory; landed in D1's fix round 1 after this
    task's brief was written, same as D2's identically named parameter)."""
    from . import delegation
    if profile not in PROFILES:
        raise ws.WorkspaceError(f"unknown permission profile {profile!r}")
    root, config = ws.load_workspace(workspace)
    if profile == "unattended":
        if not delegation.delegated_tier2(config):
            raise delegation.Refusal("tier-2-required", "the unattended profile is only for a tier 2 workspace "
                                                        "whose approver is a named delegate")
    actor = None
    if delegated:
        actor = delegation.delegated_actor(workspace, "setup", model_id=model_id, getuid=getuid, env=env,
                                           ancestors=ancestors, root_owner=root_owner)
    else:
        _owner_presence(presence, confirm)
    path = settings_path(workspace)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    current = ws._read_json(path) if path.exists() else {}
    merged = merge(current, generate(profile), profile)
    if with_hooks:
        merged = _with_hooks(merged)
    text = json.dumps(merged, indent=2, ensure_ascii=False) + "\n"
    if path.exists():
        ws._atomic_replace_text(path, text)
    else:
        ws.atomic_write_new(path, text)
    agent_readable = actor is not None or profile == "unattended"
    if agent_readable and os.name != "nt":
        path.chmod(0o644)
        path.parent.chmod(0o755)
    target = ws._inside(root, root / PROFILE_FILE)
    if agent_readable or target.exists():
        import hashlib
        sidecar = {"schema": PROFILE_SCHEMA, "profile": profile, "written_at": ws._now(),
                   "written_by": (actor or delegation.human_actor()).as_dict(),
                   "settings_sha256": hashlib.sha256(text.encode()).hexdigest()}
        body = json.dumps(sidecar, indent=2) + "\n"
        ws._atomic_replace_text(target, body) if target.exists() else ws.atomic_write_new(target, body)
        if agent_readable and os.name != "nt":
            target.chmod(0o644)
    return path
