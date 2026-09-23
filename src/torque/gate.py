"""De-identified mode: keep an AI session away from client orgs and client context.

This is a best-effort guard for an assistant following normal tool use. It scans
recognized tool calls (a Claude Code PreToolUse hook) for shapes that would reach a
Salesforce org, read or write client context, or disable the guard itself. It is not
a sandbox: a runtime-constructed command, an arbitrary script, a network tool, or a
host that does not wire up the hook can still get through. See docs/ai-access.md.
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
# Tools that read (and by default enumerate) an entire directory tree.
ALWAYS_RECURSIVE_HEADS = {"rg", "ag", "ack", "find", "fd", "tree"}
GREP_HEADS = {"grep", "egrep", "fgrep"}
# grep/rg/ag/ack/fd take PATTERN [PATH...]: their first non-flag argument is the
# search pattern, not a path. find/tree/ls take PATH[...] directly.
PATTERN_FIRST_HEADS = {"grep", "egrep", "fgrep", "rg", "ag", "ack", "fd"}
DESTRUCTIVE_VERBS = {"rm", "mv", "cp", "truncate", "shred", "unlink", "rmdir"}
PY_LAUNCHER_RE = re.compile(r"^python[23]?(\.\d+)?$")
SETTINGS_RE = re.compile(r"(^|[/\\])\.claude[/\\]settings[^/\\]*\.json$", re.IGNORECASE)
_GREP_RECURSIVE_FLAG_RE = re.compile(r"^--recursive$|^-[a-zA-Z]*[rR][a-zA-Z]*$")
_LS_RECURSIVE_FLAG_RE = re.compile(r"^--recursive$|^-[a-zA-Z]*R[a-zA-Z]*$")
_GLOB_CHARS = frozenset("*?[")
_HOME_TOKEN_RE = re.compile(r"\$\{HOME\}|\$HOME")
CASEFOLD_PLATFORMS = ("darwin", "win32")
_SPLIT_RE = re.compile(r"&&|\|\||;|\||\n|&|\(|\)|\{|\}|`")
NARROW_PATH_HINT = " Pass a narrower path, such as project/ or src/, instead of the workspace root."


def _home_value() -> str:
    """The actual HOME, in forward-slash form.

    The substituted value is spliced into a Bash command string that later
    goes through shlex.split(..., posix=True), where backslash is an escape
    character: "C:\\w/clients" splits as ["C:w/clients"], silently eating the
    separator between the drive and the rest of the path. Windows itself
    accepts forward slashes in paths, so normalizing here keeps the result
    valid POSIX-shell syntax without changing what path it names.
    """
    home = os.environ.get("HOME") or os.path.expanduser("~")
    return home.replace("\\", "/")


def _expand_home(raw: str) -> str:
    """Expand a leading ~ and any $HOME/${HOME} the same way a shell would, using
    the actual HOME so a real symlinked or nonstandard home still resolves."""
    home = _home_value()
    if raw == "~" or raw.startswith("~/"):
        raw = home + raw[1:]
    # A callable replacement is used literally; a string replacement is parsed
    # as a regex template, where backslashes are special (\1, \g<...>). A
    # Windows HOME is full of backslashes, so re.sub(pattern, home, raw) would
    # raise "bad escape" there.
    return _HOME_TOKEN_RE.sub(lambda _m: home, raw)


def _expand_home_in_command(command: str) -> str:
    """Expand $HOME/${HOME} in a whole Bash command string before it is split
    into segments. This must happen before _segments() runs: the segment
    splitter also splits on bare { and } (for brace-grouping), which would
    otherwise tear a ${HOME} reference apart before it could be recognized."""
    return _HOME_TOKEN_RE.sub(lambda _m: _home_value(), command)


def _resolve(base: Path, raw: str) -> Path:
    """Join a possibly relative tool path to the hook's cwd, then resolve symlinks
    and '..' the same way the filesystem would, so a symlinked, relative, or
    ~/$HOME-prefixed route into clients/ cannot slip past a raw string comparison."""
    raw = _expand_home(raw)
    target = Path(raw)
    if not target.is_absolute():
        target = base / target
    return Path(os.path.realpath(str(target)))


def _cf(text: str) -> str:
    return text.casefold() if sys.platform in CASEFOLD_PLATFORMS else text


def _is_within(target: Path, guarded: Path) -> bool:
    """True when target is guarded itself, or anything under it."""
    t, g = _cf(str(target)), _cf(str(guarded))
    return t == g or t.startswith(g + os.sep)


def _reaches(root: Path, guarded: Path) -> bool:
    """True when root is guarded itself, an ancestor of guarded (so a recursive
    operation rooted at root could reach it), or already inside it."""
    a, b = _cf(str(root)), _cf(str(guarded))
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
        return _is_within(_resolve(cwd, tok), clients)
    except (OSError, ValueError):
        return False


def _token_targets_claude_dir(tok: str, claude_dir: Path, cwd: Path) -> bool:
    if not tok or tok.startswith("-"):
        return False
    try:
        return _is_within(_resolve(cwd, tok), claude_dir)
    except (OSError, ValueError):
        return False


def _is_recursive_search(tok: str, rest: list[str]) -> bool:
    head = _basename(tok)
    if head in ALWAYS_RECURSIVE_HEADS:
        return True
    if head in GREP_HEADS:
        return any(_GREP_RECURSIVE_FLAG_RE.match(t) for t in rest)
    if head == "ls":
        return any(_LS_RECURSIVE_FLAG_RE.match(t) for t in rest)
    return False


def _recursive_search_targets(head: str, rest: list[str]) -> list[str]:
    """The path arguments a recursive search tool will actually read, as best we
    can tell from its usual grammar. grep/rg/ag/ack/fd take PATTERN [PATH...], so
    their first non-flag argument is the search pattern, not a path; find/tree/ls
    take PATH[...] directly, so every non-flag argument is a candidate path."""
    non_flags = [t for t in rest if not t.startswith("-")]
    if head in PATTERN_FIRST_HEADS and non_flags:
        non_flags = non_flags[1:]
    return non_flags


def _recursive_search_reaches(head: str, rest: list[str], clients: Path, cwd: Path) -> bool:
    """A recursive search tool's target defaults to cwd when it names no path
    argument at all (e.g. `rg foo`, bare `tree`)."""
    roots = _recursive_search_targets(head, rest) or ["."]
    for raw in roots:
        try:
            resolved = _resolve(cwd, raw)
        except (OSError, ValueError):
            continue
        if _reaches(resolved, clients):
            return True
    return False


def _has_glob_char(tok: str) -> bool:
    return any(c in _GLOB_CHARS for c in tok)


def _glob_targets_root(tok: str, workspace: Path, cwd: Path) -> bool:
    if not _has_glob_char(tok):
        return False
    parent_raw = tok.rsplit("/", 1)[0] if "/" in tok else "."
    try:
        parent = _resolve(cwd, parent_raw)
    except (OSError, ValueError):
        return False
    return _cf(str(parent)) == _cf(str(workspace))


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


def _block_command(toks: list[str], clients: Path, claude_dir: Path, workspace: Path, cwd: Path) -> str:
    """Scan every token (not just the head) so a wrapper, an env-var prefix, or a
    grouping construct cannot hide an org call, a client-context command, or a
    self-disable attempt behind it."""
    n = len(toks)
    destructive = any(_basename(t) in DESTRUCTIVE_VERBS for t in toks) or any(t in (">", ">>") for t in toks)
    if destructive:
        for t in toks:
            if _glob_targets_root(t, workspace, cwd):
                return ("this command uses a glob at the workspace root that could remove or "
                        "overwrite workspace.json or the hook configuration")
    for i, tok in enumerate(toks):
        rest = toks[i + 1:]
        if _is_sf_token(tok):
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
        elif _is_recursive_search(tok, rest):
            if _recursive_search_reaches(_basename(tok), rest, clients, cwd):
                return "this command can search client context recursively." + NARROW_PATH_HINT
        if _targets_guarded_file(tok):
            return "this command targets workspace.json or the hook configuration"
        if _token_targets_claude_dir(tok, claude_dir, cwd):
            return "this command targets the .claude hook configuration directory"
        if _token_is_client_path(tok, clients, cwd):
            return "this command reaches client context"
    return ""


def _scan_bash(command: str, clients: Path, claude_dir: Path, workspace: Path, cwd: Path, _depth: int = 0) -> str:
    """Check every segment of command, then recurse into $(...) / backtick
    substitutions (already exposed as their own segments by the paren/backtick
    splitter, and re-checked explicitly below for robustness) and into the string
    argument of bash -c / sh -c / zsh -c."""
    if _depth > 8 or not command:
        return ""
    command = _expand_home_in_command(command)
    for toks in _segments(command):
        reason = _block_command(toks, clients, claude_dir, workspace, cwd)
        if reason:
            return reason
        for i in range(len(toks) - 2):
            if _basename(toks[i]) in SHELL_HEADS and toks[i + 1] == "-c":
                reason = _scan_bash(toks[i + 2], clients, claude_dir, workspace, cwd, _depth + 1)
                if reason:
                    return reason
    for nested in _direct_substitutions(command):
        reason = _scan_bash(nested, clients, claude_dir, workspace, cwd, _depth + 1)
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
    claude_dir = Path(os.path.realpath(str(workspace / ".claude")))
    if tool_name == "Bash":
        reason = _scan_bash(str(tool_input.get("command", "")), clients, claude_dir, workspace, cwd)
        if reason:
            return False, f"De-identified mode: {reason}. Run it yourself outside the AI session."
        return True, ""
    if tool_name in ("Grep", "Glob"):
        raw_path = tool_input.get("path")
        root = _resolve(cwd, str(raw_path)) if raw_path else cwd
        if _reaches(root, clients):
            return False, "De-identified mode: client context stays out of the AI session." + NARROW_PATH_HINT
        pattern = str(tool_input.get("pattern") or "")
        glob_field = str(tool_input.get("glob") or "")
        mentions_clients = "clients" in pattern.casefold() or "clients" in glob_field.casefold()
        if mentions_clients and _reaches(cwd, clients):
            return False, "De-identified mode: client context stays out of the AI session." + NARROW_PATH_HINT
        return True, ""
    key = PATH_TOOLS.get(tool_name)
    if key and tool_input.get(key):
        target = _resolve(cwd, str(tool_input[key]))
        if _is_within(target, clients):
            return False, "De-identified mode: client context stays out of the AI session."
        if tool_name != "Read" and _targets_guarded_file(target.as_posix()):
            return False, "De-identified mode: only the owner changes workspace.json or the hook configuration."
    return True, ""


def _resolve_ai_access(value: object) -> str:
    """A missing key, or an explicit "full", means full, matching the documented
    default. Only an explicit "build-only" turns the gate on; anything else
    present (a typo, wrong case, a non-string) fails closed to build-only."""
    if value is None or value == "full":
        return "full"
    return "build-only"


def _home_dir() -> Path:
    home = os.environ.get("HOME") or os.path.expanduser("~")
    return Path(os.path.realpath(home))


def _is_workspace_marker(folder: Path) -> bool:
    """True only for a real Torque workspace root: both clients/ and the
    .torque/templates.json manifest that `torque workspace init` writes. A
    bare clients/ (any plain repo can have one) or a bare .torque/ (older
    torque tooling keeps unrelated state there, e.g. under $HOME) must not,
    on their own, make a folder look like a workspace with a missing config."""
    return (folder / "clients").is_dir() and (folder / ".torque" / "templates.json").is_file()


def _workspace_mode(start: Path) -> tuple[Path, str, bool]:
    """Walk up from start for the nearest workspace.json. Returns (folder, mode,
    mode_known). mode_known is False when a workspace.json was found but could
    not be read or parsed as a JSON object, or when a real workspace marker
    (_is_workspace_marker) exists with no readable workspace.json alongside it;
    callers must treat that as build-only. The search stops before the user's
    home directory (exclusive): home itself, and anything above it, is never
    treated as or searched for a workspace, since unrelated per-user state
    (e.g. an older torque tooling directory) can live directly under home."""
    home = _cf(str(_home_dir()))
    for folder in [start, *start.parents]:
        if _cf(str(folder)) == home:
            break
        config = folder / "workspace.json"
        if config.is_file():
            try:
                data = json.loads(config.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("workspace.json must be a JSON object")
            except (OSError, ValueError):
                return folder, "build-only", False
            return folder, _resolve_ai_access(data.get("ai_access")), True
        if _is_workspace_marker(folder):
            # A real workspace marker with no readable workspace.json
            # alongside it: the config may have been removed. Fail closed
            # rather than treating this folder as "no workspace here."
            return folder, "build-only", False
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
