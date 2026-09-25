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


def generate() -> dict:
    """The `permissions` object for WORKSPACE/.claude/settings.json. Adds no allow rule."""
    return {"ask": list(ASK_RULES), "deny": list(dict.fromkeys(deny_rules())), BYPASS_KEY: "disable"}


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


def merge(settings: dict, generated: dict) -> dict:
    """The settings with the generated rules added, allow rules for those routes
    removed, the user's other rules kept, and bypass mode disabled."""
    out = dict(settings)
    perms = dict(out.get("permissions") or {})
    for key in ("ask", "deny"):
        existing = perms.get(key) if isinstance(perms.get(key), list) else []
        perms[key] = list(dict.fromkeys([*existing, *generated[key]]))
    allow = perms.get("allow") if isinstance(perms.get("allow"), list) else []
    perms["allow"] = [r for r in allow if not (isinstance(r, str) and _covered(r))]
    perms[BYPASS_KEY] = generated[BYPASS_KEY]
    if perms.get("defaultMode") in SKIPPING_MODES:
        perms.pop("defaultMode")
    out["permissions"] = perms
    return out


def drift(settings: dict, generated: dict) -> list[str]:
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


def settings_path(workspace) -> Path:
    root, _ = ws.load_workspace(workspace)
    return ws._inside(root, root / ".claude" / "settings.json")


def write_settings(workspace, presence=None, confirm=None) -> Path:
    """Merge the generated rules into WORKSPACE/.claude/settings.json (owner only)."""
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
    path = settings_path(workspace)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    current = ws._read_json(path) if path.exists() else {}
    text = json.dumps(merge(current, generate()), indent=2, ensure_ascii=False) + "\n"
    if path.exists():
        ws._atomic_replace_text(path, text)
    else:
        ws.atomic_write_new(path, text)
    return path
