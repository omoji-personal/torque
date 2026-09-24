"""Claude Code permission rules for a connected workspace: ask before every
Salesforce and Torque write route, every interpreter and runner and every browser
server; deny approval administration and edits to mode, consent, approval and
Salesforce CLI files; never allow bypass mode. The host enforces these rules; the
gate enforces its own checks as well (docs/connected-approval.md)."""
from __future__ import annotations

import json
from pathlib import Path

from . import workspace as ws

SF_WRITE_PREFIXES = (
    "sf project deploy start", "sf project deploy quick", "sf project deploy resume", "sf project delete",
    "sf project reset", "sf data create", "sf data update", "sf data upsert", "sf data delete",
    "sf data import", "sf apex run", "sf org assign", "sf org create", "sf org delete", "sf org refresh",
    "sf package install", "sf package uninstall", "sf api request", "sf community publish", "sf agent",
    "sfdx",
)
TORQUE_WRITE_PREFIXES = ("torque deploy", "torque data", "torque org", "torque recover", "torque revert",
                         "torque browser", "torque qa", "torque probes", "jsc")
INTERPRETERS = ("python", "python3", "py", "node", "deno", "bun", "ruby", "perl", "php", "bash", "sh", "zsh",
                "pwsh", "osascript", "npx", "npm", "pnpm", "yarn", "make", "curl", "wget", "xargs", "eval",
                "source", "claude")
BROWSER_SERVERS = ("mcp__claude-in-chrome", "mcp__chrome-devtools", "mcp__playwright", "mcp__computer-use")
ASK_RULES = tuple([f"Bash({p}:*)" for p in (*SF_WRITE_PREFIXES, *TORQUE_WRITE_PREFIXES, *INTERPRETERS)]
                  + list(BROWSER_SERVERS))
DENY_RULES = (
    "Bash(torque approval grant:*)", "Bash(torque approval deny:*)",
    "Bash(torque approval permissions *--write*)",
    "Bash(torque client consent record:*)", "Bash(torque client consent sign-off:*)",
    "Bash(torque client consent suspend:*)", "Bash(torque launch:*)", "Bash(torque workspace ai-access:*)",
    "Bash(sf alias set:*)", "Bash(sf alias unset:*)", "Bash(sf config set:*)", "Bash(sf config unset:*)",
    "Edit(/workspace.json)", "Edit(/clients/*/consent.json)", "Edit(/clients/*/consent-evidence/**)",
    "Edit(/clients/*/approvals/**)", "Edit(/.claude/**)",
    "Read(~/.config/torque/approval.key)", "Edit(~/.config/torque/approval.key)",
    "Read(~/.sfdx/**)", "Edit(~/.sfdx/**)", "Read(~/.sf/**)", "Edit(~/.sf/**)",
    "Edit(~/.local/share/sf/**)", "Edit(~/.claude/settings*.json)",
)
BYPASS_KEY = "disableBypassPermissionsMode"


def generate() -> dict:
    """The `permissions` object for WORKSPACE/.claude/settings.json. Adds no allow rule."""
    return {"ask": list(ASK_RULES), "deny": list(DENY_RULES), BYPASS_KEY: "disable"}


def _prefix(rule: str) -> tuple[str, str] | None:
    """(tool, command prefix) of a Bash rule, without its wildcard."""
    if not rule.endswith(")") or "(" not in rule:
        return None
    tool, body = rule[:-1].split("(", 1)
    for suffix in (":*", " *", "*"):
        if body.endswith(suffix):
            body = body[:-len(suffix)]
            break
    return tool, body.strip()


def _covered(rule: str) -> bool:
    """True when an allow rule names a route the generated rules ask about or deny."""
    if rule in ASK_RULES or rule in DENY_RULES:
        return True
    if any(rule == server or rule.startswith(server + "__") for server in BROWSER_SERVERS):
        return True
    mine = _prefix(rule)
    if mine is None:
        return rule in ("Bash", "Bash(*)")
    tool, body = mine
    if tool == "Bash" and body in ("", "*"):
        return True
    for generated in ASK_RULES + DENY_RULES:
        theirs = _prefix(generated)
        if theirs and theirs[0] == tool and "*" not in theirs[1] and (
                body == theirs[1] or body.startswith(theirs[1] + " ") or theirs[1].startswith(body + " ")
                or theirs[1] == body):
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
    if perms.get("defaultMode") in ("bypassPermissions", "auto", "dontAsk"):
        perms.pop("defaultMode")
    out["permissions"] = perms
    return out


def drift(settings: dict, generated: dict) -> list[str]:
    """Readiness problems: missing generated rules, allow rules for their routes
    (the ask still wins, but the allow shows an intent to skip it), bypass mode."""
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
    if perms.get("defaultMode") in ("bypassPermissions", "auto", "dontAsk"):
        problems.append(f"defaultMode is {perms['defaultMode']}, which skips the consultant's prompts")
    return problems


def settings_path(workspace) -> Path:
    root, _ = ws.load_workspace(workspace)
    return ws._inside(root, root / ".claude" / "settings.json")


def write_settings(workspace, presence=None) -> Path:
    """Merge the generated rules into WORKSPACE/.claude/settings.json (owner only)."""
    if presence is None:
        from .presence import operator_present as presence
    check = presence()
    if not check.ok:
        raise ws.WorkspaceError(f"the owner writes permission rules at a real terminal: {check.reason}")
    path = settings_path(workspace)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    current = ws._read_json(path) if path.exists() else {}
    text = json.dumps(merge(current, generate()), indent=2, ensure_ascii=False) + "\n"
    if path.exists():
        ws._atomic_replace_text(path, text)
    else:
        ws.atomic_write_new(path, text)
    return path
