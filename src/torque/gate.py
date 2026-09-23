"""De-identified mode: keep an AI session away from client orgs and client context.

This is a best-effort guard for an assistant following normal tool use. It scans
recognized tool calls (a Claude Code PreToolUse hook) for shapes that would reach a
Salesforce org, read or write client context, or disable the guard itself. It is not
a sandbox: a script that builds a command at runtime, a network tool, or a host that
does not wire up the hook can still get through. See docs/ai-access.md.
"""
from __future__ import annotations
import json
import os
import re
import shlex
import sys
from pathlib import Path

# sf/sfdx subcommands (or bare flags) that stay local and never touch an org, as
# long as no org flag (ORG_FLAGS) also appears anywhere on the line.
SF_LOCAL = {("project", "generate"), ("lightning", "generate"), ("apex", "generate"),
            ("--version",), ("version",), ("help",), ("plugins",)}
ORG_FLAGS = {"-o", "--target-org", "--from-org", "-u", "--targetusername",
             "--target-dev-hub", "-v"}
TORQUE_ALLOWED = {"demo", "workflows", "doctor", "--version", "--help", "-h"}
PATH_TOOLS = {"Read": "file_path", "Edit": "file_path", "Write": "file_path",
              "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
SHELL_HEADS = {"bash", "sh", "zsh"}
PY_LAUNCHER_RE = re.compile(r"^python[23]?(\.\d+)?$")
SETTINGS_RE = re.compile(r"(^|[/\\])\.claude[/\\]settings[^/\\]*\.json$", re.IGNORECASE)
CASEFOLD_PLATFORMS = ("darwin", "win32")
_SPLIT_RE = re.compile(r"&&|\|\||;|\||\n|&|\(|\)|\{|\}|`")


def _resolve(base: Path, raw: str) -> Path:
    """Join a possibly relative tool path to the hook's cwd, then resolve symlinks
    and '..' the same way the filesystem would, so a symlinked or relative route
    into clients/ cannot slip past a raw string comparison."""
    target = Path(raw)
    if not target.is_absolute():
        target = base / target
    return Path(os.path.realpath(str(target)))


def _cf(text: str) -> str:
    return text.casefold() if sys.platform in CASEFOLD_PLATFORMS else text


def _is_client_path(target: Path, clients: Path) -> bool:
    t, c = _cf(str(target)), _cf(str(clients))
    return t == c or t.startswith(c + os.sep)


def _cwd_reaches_clients(cwd: Path, clients: Path) -> bool:
    """True when a recursive search rooted at cwd could reach clients/, or cwd is
    already inside it: cwd at or above clients, or already within it."""
    a, b = _cf(str(cwd)), _cf(str(clients))
    return a == b or b.startswith(a + os.sep) or a.startswith(b + os.sep)


def _targets_guarded_file(text: str) -> bool:
    """True if text (a raw Bash token, or a resolved path) names workspace.json or
    a .claude/settings*.json hook config, case-insensitively. Only the owner
    changes these; an AI session must not be able to switch the mode off."""
    if "workspace.json" in text.casefold():
        return True
    return bool(SETTINGS_RE.search(text))


def _basename(tok: str) -> str:
    name = re.split(r"[/\\]", tok)[-1].lower()
    return re.sub(r"\.(cmd|exe|bat)$", "", name)


def _is_sf_token(tok: str) -> bool:
    if tok.casefold() == "@salesforce/cli":
        return True
    return _basename(tok) in ("sf", "sfdx")


def _has_org_flag(rest: list[str]) -> bool:
    return any(tok.split("=", 1)[0] in ORG_FLAGS for tok in rest)


def _sf_local_ok(rest: list[str]) -> bool:
    head2 = tuple(t for t in rest[:2] if not t.startswith("-") or t == "--version")
    if head2[:2] in SF_LOCAL:
        return True
    if head2[:1] in SF_LOCAL:
        # A bare single-word local command (plugins, version, help, --version)
        # must not carry a further subcommand, e.g. "plugins install <name>".
        return not [t for t in rest[1:] if not t.startswith("-")]
    return False


def _token_is_client_path(tok: str, clients: Path, cwd: Path) -> bool:
    if not tok or tok.startswith("-"):
        return False
    try:
        return _is_client_path(_resolve(cwd, tok), clients)
    except (OSError, ValueError):
        return False


def _segments(command: str):
    for part in _SPLIT_RE.split(command):
        part = part.strip()
        if not part:
            continue
        try:
            toks = shlex.split(part, posix=True)
        except ValueError:
            toks = part.split()
        if toks:
            yield toks


def _block_command(toks: list[str], clients: Path, cwd: Path) -> str:
    """Scan every token (not just the head) so a wrapper, an env-var prefix, or a
    grouping construct cannot hide an org call, a client-context command, or a
    self-disable attempt behind it."""
    n = len(toks)
    for i, tok in enumerate(toks):
        if _is_sf_token(tok):
            rest = toks[i + 1:]
            if _has_org_flag(rest):
                return f"{_basename(tok) or 'sf'} {' '.join(rest[:2])} can reach a Salesforce org"
            if not _sf_local_ok(rest):
                return f"{_basename(tok) or 'sf'} {' '.join(rest[:2])} can reach a Salesforce org"
        elif _basename(tok) == "torque":
            sub = toks[i + 1] if i + 1 < n else None
            if sub is not None and sub not in TORQUE_ALLOWED:
                return f"torque {sub} reads client context or an org"
        elif (PY_LAUNCHER_RE.match(_basename(tok)) and i + 2 < n
              and toks[i + 1] == "-m" and toks[i + 2] in ("torque", "torque.cli")):
            sub = toks[i + 3] if i + 3 < n else None
            if sub is not None and sub not in TORQUE_ALLOWED:
                return f"torque {sub} reads client context or an org"
        if _targets_guarded_file(tok):
            return "this command targets workspace.json or the hook configuration"
        if _token_is_client_path(tok, clients, cwd):
            return "this command reaches client context"
    return ""


def _scan_bash(command: str, clients: Path, cwd: Path, _depth: int = 0) -> str:
    """Check every segment of command, then recurse into $(...) / backtick
    substitutions (already exposed as their own segments by the paren/backtick
    splitter, and re-checked explicitly below for robustness) and into the string
    argument of bash -c / sh -c / zsh -c."""
    if _depth > 8 or not command:
        return ""
    for toks in _segments(command):
        reason = _block_command(toks, clients, cwd)
        if reason:
            return reason
        for i in range(len(toks) - 2):
            if _basename(toks[i]) in SHELL_HEADS and toks[i + 1] == "-c":
                reason = _scan_bash(toks[i + 2], clients, cwd, _depth + 1)
                if reason:
                    return reason
    for nested in _direct_substitutions(command):
        reason = _scan_bash(nested, clients, cwd, _depth + 1)
        if reason:
            return reason
    return ""


def _direct_substitutions(command: str) -> list[str]:
    """Return the immediate contents of every top-level $(...) and `...` span."""
    found = [m.group(1) for m in re.finditer(r"`([^`]*)`", command)]
    i = 0
    while True:
        idx = command.find("$(", i)
        if idx == -1:
            break
        depth, j = 1, idx + 2
        while j < len(command) and depth:
            if command[j] == "(":
                depth += 1
            elif command[j] == ")":
                depth -= 1
            j += 1
        found.append(command[idx + 2:max(idx + 2, j - 1)])
        i = j
    return found


def decide(tool_name: str, tool_input: dict, workspace: Path, mode: str,
           cwd: Path | None = None) -> tuple[bool, str]:
    if mode != "build-only":
        return True, ""
    workspace = Path(os.path.realpath(str(workspace)))
    cwd = Path(os.path.realpath(str(cwd))) if cwd is not None else workspace
    clients = Path(os.path.realpath(str(workspace / "clients")))
    if tool_name == "Bash":
        reason = _scan_bash(str(tool_input.get("command", "")), clients, cwd)
        if reason:
            return False, f"De-identified mode: {reason}. Run it yourself outside the AI session."
        return True, ""
    if tool_name in ("Grep", "Glob"):
        raw_path = tool_input.get("path")
        if raw_path:
            if _is_client_path(_resolve(cwd, str(raw_path)), clients):
                return False, "De-identified mode: client context stays out of the AI session."
        pattern = str(tool_input.get("pattern") or tool_input.get("glob") or "")
        if _cwd_reaches_clients(cwd, clients) and (not raw_path or "clients" in pattern.casefold()):
            return False, "De-identified mode: client context stays out of the AI session."
        return True, ""
    key = PATH_TOOLS.get(tool_name)
    if key and tool_input.get(key):
        target = _resolve(cwd, str(tool_input[key]))
        if _is_client_path(target, clients):
            return False, "De-identified mode: client context stays out of the AI session."
        if tool_name != "Read" and _targets_guarded_file(target.as_posix()):
            return False, "De-identified mode: only the owner changes workspace.json or the hook configuration."
    return True, ""


def _workspace_mode(start: Path) -> tuple[Path, str, bool]:
    """Walk up from start for the nearest workspace.json. Returns (folder, mode,
    mode_known). mode_known is False when a workspace.json was found but could not
    be read or parsed as a JSON object; callers must treat that as build-only."""
    for folder in [start, *start.parents]:
        config = folder / "workspace.json"
        if config.is_file():
            try:
                data = json.loads(config.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("workspace.json must be a JSON object")
            except (OSError, ValueError):
                return folder, "build-only", False
            mode = data.get("ai_access")
            # Anything other than exactly "full" (missing, a typo, wrong case, a
            # non-string) fails closed to build-only.
            return folder, ("full" if mode == "full" else "build-only"), True
    return start, "full", True


def main() -> int:
    try:
        event = json.loads(sys.stdin.read())
        if not isinstance(event, dict):
            raise ValueError("hook input must be a JSON object")
        if "tool_name" not in event:
            raise ValueError("hook input has no tool_name")
        cwd = Path(os.path.realpath(str(event.get("cwd") or ".")))
        root, mode, _ = _workspace_mode(cwd)
        tool_input = event.get("tool_input")
        if tool_input is None:
            tool_input = {}
        if not isinstance(tool_input, dict):
            raise ValueError("tool_input must be an object")
        allowed, reason = decide(str(event.get("tool_name", "")), tool_input, root, mode, cwd)
    except Exception as exc:
        # Any failure here means this call could not be safely evaluated. Since
        # decide() only does real work in build-only mode (it returns immediately
        # otherwise), a failure here is only reachable when the mode could be
        # build-only, so this fails closed rather than letting the tool run.
        print(f"De-identified mode: could not safely evaluate this call ({exc}); blocking to fail closed.",
              file=sys.stderr)
        return 2
    if not allowed:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
