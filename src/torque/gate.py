"""De-identified mode: keep an AI session away from client orgs and client context.

This is a best-effort guard for an assistant following normal tool use. It scans
recognized tool calls (a Claude Code PreToolUse hook) for shapes that would reach a
Salesforce org, read or write client context, or disable the guard itself. It is not
a sandbox: a runtime-constructed command, an arbitrary script, a network tool, or a
host that does not wire up the hook can still get through. See docs/ai-access.md.
"""
from __future__ import annotations
import glob
import itertools
import json
import os
import re
import shlex
import sys
from functools import lru_cache
from pathlib import Path

# sf/sfdx subcommands (or bare flags) that stay local and never touch an org, as
# long as no org flag (ORG_FLAGS) also appears anywhere on the line.
SF_LOCAL = {("project", "generate"), ("lightning", "generate"), ("apex", "generate"),
            ("project", "convert"), ("code-analyzer", "run"), ("code-analyzer", "rules"),
            ("--version",), ("version",), ("help",), ("plugins",)}
# Local sf commands that read a directory tree, with the flags naming their roots.
# With no such flag the root is the current directory. The roots must not reach
# clients/.
SF_TREE_READERS = {("code-analyzer", "run"): {"--workspace", "-w", "--target", "-t"},
                   ("code-analyzer", "rules"): {"--workspace", "-w", "--target", "-t"}}
ORG_FLAGS = {"-o", "--target-org", "--from-org", "-u", "--targetusername",
             "--target-dev-hub", "-v"}
TORQUE_ALLOWED = {"demo", "workflows", "doctor", "--version", "--help", "-h"}
# Every console script this distribution installs ([project.scripts] in
# pyproject.toml), classified. "torque" is checked against TORQUE_ALLOWED. Every
# other script is a legacy delegate (the same code `torque data/deploy/org/qa/...`
# forwards to) that reaches an org or client state, so only its help and version
# forms are allowed. tests/test_gate.py fails when pyproject gains a script that
# is not classified here.
CONSOLE_SCRIPTS = {
    "torque": "torque",
    "jsc": "delegate",
    "jsc-advisory": "delegate",
    "jsc-qa": "delegate",
    "jsc-browser-tests": "delegate",
    "jsc-memory": "delegate",
    "jsc-loganalyzer": "delegate",
    "jsc-probes": "delegate",
    "meeting-processor": "delegate",
    "jsc-ai-prompt-regression": "delegate",
}
DELEGATE_ALLOWED = {"--help", "-h", "--version"}
# Top-level packages behind the delegate scripts (and their shared library).
# `python -m` on any module under one of them is treated as a delegate call.
DELEGATE_MODULE_RE = re.compile(r"^(jsc_[A-Za-z0-9_]+|meeting_processor)(\.|$)")
# `python -m torque` / `python -m torque.cli` follow TORQUE_ALLOWED; the hook
# module itself is harmless; any other torque submodule run as a script is blocked.
TORQUE_MAIN_MODULES = {"torque", "torque.cli"}
TORQUE_HARMLESS_MODULES = {"torque.gate"}
# A group of CPython single-letter flags that take no argument, ending in -m,
# with the module either joined (-mtorque, -Imtorque) or as the next token.
_PY_M_FLAG_RE = re.compile(r"^-[bBdEhiIOPqsSuvx]*m(.*)$")
_PY_ARG_FLAGS = {"-W", "-X", "--check-hash-based-pycs"}
# MCP tool names ("mcp__<server>__<tool>") that indicate Salesforce org access.
_MCP_SF_SUBSTRINGS = ("salesforce", "sfdx", "sf_", "_sf", "soql", "sosl", "sobject", "apex")
_MCP_SF_TOKEN_RE = re.compile(r"(^|[_\-.])sf([_\-.]|$)")
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
PY_LAUNCHER_RE = re.compile(r"^(python[23]?(\.\d+)?|pythonw|py)$")
SETTINGS_RE = re.compile(r"(^|[/\\])\.claude[/\\]settings[^/\\]*\.json$", re.IGNORECASE)
_GREP_RECURSIVE_FLAG_RE = re.compile(r"^--recursive$|^-[a-zA-Z]*[rR][a-zA-Z]*$")
_LS_RECURSIVE_FLAG_RE = re.compile(r"^--recursive$|^-[a-zA-Z]*R[a-zA-Z]*$")
# Copy and archive tools that read a whole directory tree when given a recursive flag
# (tar always does).
RECURSIVE_COPY_HEADS = {"cp", "scp", "rsync", "zip"}
_COPY_RECURSIVE_FLAG_RE = re.compile(r"^--(recursive|archive)$|^-[a-zA-Z]*[rRa][a-zA-Z]*$")
_GLOB_CHARS = frozenset("*?[")
_HOME_TOKEN_RE = re.compile(r"\$\{HOME\}|\$HOME")
CASEFOLD_PLATFORMS = ("darwin", "win32")
_SPLIT_RE = re.compile(r"&&|\|\||;|\||\n|&|\(|\)|\{|\}|`")
_SPLIT_KEEP_RE = re.compile(r"(&&|\|\||;|\||\n|&|\(|\)|\{|\}|`)")
_GROUPING_CHARS = frozenset("(){}`")
# A shell redirection operator attached to its target: <file, 0<file, >file,
# 2>>file, &>file, <>file, <<<word.
_REDIRECT_RE = re.compile(r"^(?:\d+|&)?(?:<<<|<<-?|<>|<&|>&|>>|>\||<|>)(?P<rest>.*)$")
# NAME=value (a shell assignment, including after export/declare/local).
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Brace expansion: prefix{a,b}suffix within one shell word.
_BRACE_RE = re.compile(r"([^\s{}'\"]*)\{([^{}\s]*,[^{}\s]*)\}([^\s{}'\"]*)")
_PWD_BRACED_RE = re.compile(r"\$\{PWD\}")
_PWD_TOKEN_RE = re.compile(r"\$PWD(?![A-Za-z0-9_])")
_GLOB_LIMIT = 2000
# Package installers, and the verbs that change what is installed.
_INSTALLER_RE = re.compile(r"^(pip[0-9.]*|pipx|uv)$")
_INSTALL_VERBS = {"install", "uninstall", "remove", "reinstall", "upgrade", "sync", "add", "inject",
                  "uninject"}
_INSTALL_ALL_VERBS = {"reinstall-all", "uninstall-all", "upgrade-all"}
# Installed-distribution metadata for Torque (dist-info, editable .pth or finder).
_TORQUE_INSTALL_RE = re.compile(r"__editable__[^/\\]*torque|torque_salesforce[^/\\]*\.(dist-info|egg-info|pth|egg-link)",
                                re.IGNORECASE)
# MCP tool names that walk a directory tree from the path they are given.
_MCP_RECURSIVE_MARKERS = ("tree", "search", "find", "grep", "glob", "walk", "recursive")
_CD_HEADS = {"cd", "pushd"}
_MAX_CWDS = 32
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
    # Only on Windows: on POSIX a backslash is a legal filename character, and
    # rewriting it would silently name a different path.
    return home.replace("\\", "/") if os.name == "nt" else home


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
    if not _HOME_TOKEN_RE.search(command):
        return command
    home = _home_value()
    if "\\" in home:
        # POSIX only (Windows HOME was normalized above): a literal backslash
        # would be eaten by shlex after splicing and name a different path.
        # Fail closed rather than evaluate the wrong path.
        raise ValueError("HOME contains a backslash; cannot evaluate $HOME safely")
    return _HOME_TOKEN_RE.sub(lambda _m: home, command)


def _resolve(base: Path, raw: str) -> Path:
    """Join a possibly relative tool path to the hook's cwd, then resolve symlinks
    and '..' the same way the filesystem would, so a symlinked, relative, or
    ~/$HOME-prefixed route into clients/ cannot slip past a raw string comparison."""
    raw = _expand_home(raw)
    raw = _PWD_TOKEN_RE.sub(lambda _m: str(base), raw)
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


@lru_cache(maxsize=1)
def _package_dir() -> Path:
    """The installed torque package directory (this file's folder), resolved."""
    return Path(os.path.realpath(str(Path(__file__).parent)))


def _token_operand(tok: str) -> str:
    """The part of a raw Bash token that can name a file: the target of an
    attached redirection (<file, 2>file), the value of --flag=value, or the
    value of a NAME=value assignment. A bare flag has none."""
    m = _REDIRECT_RE.match(tok)
    if m:
        return m.group("rest")
    if tok.startswith("-"):
        return tok.split("=", 1)[1] if "=" in tok else ""
    if _ASSIGN_RE.match(tok):
        return tok.split("=", 1)[1]
    return tok


def _glob_fixed_prefix(pattern: str) -> str:
    """The leading path components of pattern that contain no glob character."""
    parts = re.split(r"[/\\]", pattern)
    fixed = []
    for part in parts:
        if _has_glob_char(part):
            break
        fixed.append(part)
    if not fixed:
        return "."
    joined = "/".join(fixed)
    return joined or "/"


def _glob_paths(operand: str, cwd: Path) -> tuple[list[Path], list[Path]]:
    """Expand a glob operand the way a shell would. Returns (matches, roots):
    matches are the concrete paths it names; roots are directories a recursive
    (**) or too-broad-to-list glob could reach below, to be treated like the
    root of a recursive search."""
    if not _has_glob_char(operand):
        return [], []
    expanded = _PWD_TOKEN_RE.sub(lambda _m: str(cwd), _expand_home(operand))
    try:
        prefix = _resolve(cwd, _glob_fixed_prefix(expanded))
    except (OSError, ValueError):
        return [], []
    if "**" in expanded:
        # zsh (and bash with globstar) recurse on **: treat it as a recursive
        # search rooted at the fixed prefix.
        return [], [prefix]
    pattern = expanded if os.path.isabs(expanded) else os.path.join(str(cwd), expanded)
    try:
        found = list(itertools.islice(glob.iglob(pattern), _GLOB_LIMIT + 1))
    except (OSError, ValueError, re.error):
        return [], [prefix]
    if len(found) > _GLOB_LIMIT:
        return [], [prefix]
    return [Path(os.path.realpath(p)) for p in found], []


def _token_paths(tok: str, cwd: Path) -> tuple[list[Path], list[Path]]:
    """Every path a raw Bash token can name from cwd, as (paths, recursive roots)."""
    operand = _token_operand(tok)
    if not operand or operand.startswith("-"):
        return [], []
    paths: list[Path] = []
    try:
        paths.append(_resolve(cwd, operand))
    except (OSError, ValueError):
        pass
    matches, roots = _glob_paths(operand, cwd)
    return paths + matches, roots


def _token_is_client_path(tok: str, clients: Path, cwd: Path) -> bool:
    if not tok:
        return False
    paths, roots = _token_paths(tok, cwd)
    return any(_is_within(p, clients) for p in paths) or any(_reaches(r, clients) for r in roots)


def _token_targets_claude_dir(tok: str, claude_dir: Path, cwd: Path) -> bool:
    if not tok:
        return False
    paths, roots = _token_paths(tok, cwd)
    return any(_is_within(p, claude_dir) for p in paths) or any(_reaches(r, claude_dir) for r in roots)


def _token_targets_package(tok: str, cwd: Path) -> bool:
    """True if a Bash token names the installed torque package (or its install
    metadata), so the session cannot edit or delete the gate itself."""
    if _TORQUE_INSTALL_RE.search(tok):
        return True
    paths, _ = _token_paths(tok, cwd)
    package = _package_dir()
    return any(_is_within(p, package) for p in paths)


def _token_names_guarded_file(tok: str, cwd: Path) -> bool:
    if _targets_guarded_file(tok):
        return True
    paths, _ = _token_paths(tok, cwd)
    return any(_targets_guarded_file(p.as_posix()) for p in paths)


def _is_recursive_search(tok: str, rest: list[str]) -> bool:
    head = _basename(tok)
    if head in ALWAYS_RECURSIVE_HEADS:
        return True
    if head in GREP_HEADS:
        return any(_GREP_RECURSIVE_FLAG_RE.match(t) for t in rest)
    if head == "ls":
        return any(_LS_RECURSIVE_FLAG_RE.match(t) for t in rest)
    if head == "tar":
        return True
    if head in RECURSIVE_COPY_HEADS:
        return any(_COPY_RECURSIVE_FLAG_RE.match(t) for t in rest)
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
    return any(_root_reaches(raw, clients, cwd) for raw in roots)


def _root_reaches(raw: str, clients: Path, cwd: Path) -> bool:
    """True when a recursive operation rooted at raw (a raw token, glob allowed)
    could reach clients/."""
    paths, roots = _token_paths(raw, cwd)
    return any(_reaches(p, clients) for p in paths + roots)


def _flag_values(args: list[str], flags: set[str]) -> list[str] | None:
    """The values given to any of flags (as --flag VALUE or --flag=VALUE), or
    None when none of them appears."""
    found: list[str] = []
    seen = False
    for j, tok in enumerate(args):
        name, eq, value = tok.partition("=")
        if name in flags:
            seen = True
            if eq:
                found.append(value)
            elif j + 1 < len(args):
                found.append(args[j + 1])
    return found if seen else None


def _sf_tree_reader_reaches(rest: list[str], clients: Path, cwd: Path) -> bool:
    """A local sf command that reads a directory tree (code-analyzer) must not
    be rooted at or above clients/. Its root defaults to the current directory."""
    head2 = tuple(t for t in rest[:2] if not t.startswith("-"))
    flags = SF_TREE_READERS.get(head2[:2])
    if flags is None:
        return False
    roots = _flag_values(rest, flags) or ["."]
    return any(_root_reaches(raw, clients, cwd) for raw in roots)


def _git_grep_reaches(rest: list[str], clients: Path, cwd: Path) -> bool:
    """`git grep --untracked` and `git grep --no-index` read files Git does not
    track, and clients/ is git-ignored, so either form must not be rooted at or
    above clients/. Plain `git grep` reads tracked files only."""
    base = cwd
    j = 0
    while j < len(rest):
        tok = rest[j]
        if tok == "-C" and j + 1 < len(rest):
            try:
                base = _resolve(base, rest[j + 1])
            except (OSError, ValueError):
                return True
            j += 2
            continue
        if tok in ("-c", "--git-dir", "--work-tree", "--namespace") and j + 1 < len(rest):
            j += 2
            continue
        if tok.startswith("-"):
            j += 1
            continue
        break
    if j >= len(rest) or rest[j] != "grep":
        return False
    args = rest[j + 1:]
    if not any(t in ("--untracked", "--no-index") for t in args):
        return False
    if "--" in args:
        paths = list(args[args.index("--") + 1:])
    else:
        non_flags: list[str] = []
        explicit_pattern = False
        k = 0
        while k < len(args):
            tok = args[k]
            if tok in _GIT_GREP_PATTERN_FLAGS:
                explicit_pattern = True
                k += 2
                continue
            if tok in _GIT_GREP_VALUE_FLAGS:
                k += 2
                continue
            if tok.startswith(("--regexp=", "--file=")) or re.match(r"^-[ef].", tok):
                explicit_pattern = True
            elif not tok.startswith("-"):
                non_flags.append(tok)
            k += 1
        paths = non_flags if explicit_pattern else non_flags[1:]
    return any(_root_reaches(raw, clients, base) for raw in (paths or ["."]))


_GIT_GREP_PATTERN_FLAGS = {"-e", "-f", "--regexp", "--file"}
_GIT_GREP_VALUE_FLAGS = {"-A", "-B", "-C", "--after-context", "--before-context", "--context",
                         "-m", "--max-count", "--max-depth", "--threads", "-O", "--open-files-in-pager"}


def _installer_reason(name: str, args: list[str]) -> str:
    """pip / uv / pipx changing an installed Torque (or every tool at once)."""
    if any(t in _INSTALL_ALL_VERBS for t in args):
        return f"{name} {' '.join(args[:2])} can remove or replace the installed Torque gate"
    if any(t in _INSTALL_VERBS for t in args) and any("torque" in t.casefold() for t in args):
        return f"{name} {' '.join(args[:2])} can remove or replace the installed Torque gate"
    return ""


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


def _python_module(rest: list[str]) -> tuple[str | None, int]:
    """For the arguments after a python launcher, return (module, index of the
    first argument after the module) when the call is `python ... -m MODULE`,
    including the joined `-mMODULE` and grouped `-Im MODULE` forms. Returns
    (None, -1) for a script path, -c code, or no -m at all."""
    j = 0
    while j < len(rest):
        tok = rest[j]
        if tok in _PY_ARG_FLAGS:
            j += 2
            continue
        m = _PY_M_FLAG_RE.match(tok)
        if m:
            if m.group(1):
                return m.group(1), j + 1
            if j + 1 < len(rest):
                return rest[j + 1], j + 2
            return None, -1
        if tok == "-c" or not tok.startswith("-"):
            return None, -1
        j += 1
    return None, -1


def _torque_reason(args: list[str]) -> str:
    """The torque allowlist, shared by the console script and `python -m torque`."""
    sub = args[0] if args else None
    if sub is not None and sub not in TORQUE_ALLOWED:
        return f"torque {sub} reads client context or an org"
    if sub == "doctor" and any(t.split("=", 1)[0] == "--client" for t in args[1:]):
        return "torque doctor --client reads client context"
    return ""


def _delegate_reason(name: str, args: list[str]) -> str:
    """A legacy delegate script or module: only help and version forms pass."""
    sub = args[0] if args else None
    if sub in DELEGATE_ALLOWED:
        return ""
    shown = f"{name} {sub}" if sub else name
    return f"{shown} can reach a Salesforce org or client context"


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


def _without_redirections(toks: list[str]) -> list[str]:
    """The command words, without redirections (<file, 2>/dev/null, > out) so
    a redirection target is not mistaken for a subcommand or argument. Every
    original token is still checked as a possible path."""
    words: list[str] = []
    skip = False
    for tok in toks:
        if skip:
            skip = False
            continue
        m = _REDIRECT_RE.match(tok)
        if m:
            skip = not m.group("rest")
            continue
        words.append(tok)
    return words


def _block_command(toks: list[str], clients: Path, claude_dir: Path, workspace: Path, cwd: Path) -> str:
    """Scan every token (not just the head) so a wrapper, an env-var prefix, or a
    grouping construct cannot hide an org call, a client-context command, or a
    self-disable attempt behind it."""
    destructive = (any(_basename(t) in DESTRUCTIVE_VERBS for t in toks)
                   or any(re.match(r"^(\d+|&)?>", t) for t in toks))
    if destructive:
        for t in toks:
            if _glob_targets_root(_token_operand(t) or t, workspace, cwd):
                return ("this command uses a glob at the workspace root that could remove or "
                        "overwrite workspace.json or the hook configuration")
    words = _without_redirections(toks)
    for i, tok in enumerate(words):
        rest = words[i + 1:]
        if _is_sf_token(tok):
            if _has_org_flag(rest):
                return f"{_basename(tok) or 'sf'} {' '.join(rest[:2])} can reach a Salesforce org"
            if not _sf_local_ok(rest):
                return f"{_basename(tok) or 'sf'} {' '.join(rest[:2])} can reach a Salesforce org"
            if _sf_tree_reader_reaches(rest, clients, cwd):
                return (f"{_basename(tok) or 'sf'} {' '.join(rest[:2])} would read client context."
                        + NARROW_PATH_HINT)
        elif _basename(tok) == "git" and _git_grep_reaches(rest, clients, cwd):
            return "git grep over untracked files can read client context." + NARROW_PATH_HINT
        elif _INSTALLER_RE.match(_basename(tok)):
            reason = _installer_reason(_basename(tok), rest)
            if reason:
                return reason
        elif _basename(tok) in CONSOLE_SCRIPTS:
            name = _basename(tok)
            reason = _torque_reason(rest) if CONSOLE_SCRIPTS[name] == "torque" else _delegate_reason(name, rest)
            if reason:
                return reason
        elif PY_LAUNCHER_RE.match(_basename(tok)) and _python_module(rest)[0]:
            module, after = _python_module(rest)
            args = rest[after:]
            reason = ""
            if module in TORQUE_MAIN_MODULES:
                reason = _torque_reason(args)
            elif module == "pip":
                reason = _installer_reason("python -m pip", args)
            elif DELEGATE_MODULE_RE.match(module):
                reason = _delegate_reason(f"python -m {module}", args)
            elif (module == "torque" or module.startswith("torque.")) and module not in TORQUE_HARMLESS_MODULES:
                reason = f"python -m {module} is not an allowed torque entry point"
            if reason:
                return reason
        elif _is_recursive_search(tok, rest):
            if _recursive_search_reaches(_basename(tok), rest, clients, cwd):
                return "this command can search client context recursively." + NARROW_PATH_HINT
    for tok in toks:
        if _token_names_guarded_file(tok, cwd):
            return "this command targets workspace.json or the hook configuration"
        if _token_targets_claude_dir(tok, claude_dir, cwd):
            return "this command targets the .claude hook configuration directory"
        if _token_targets_package(tok, cwd):
            return "this command targets the installed Torque package that enforces this mode"
        if _token_is_client_path(tok, clients, cwd):
            return "this command reaches client context"
    return ""


def _expand_braces(command: str) -> str:
    """Expand prefix{a,b}suffix words into separate words, as the shell would,
    before the segment splitter (which also splits on { and }) tears them apart."""
    for _ in range(8):
        expanded = _BRACE_RE.sub(
            lambda m: " ".join(m.group(1) + alt + m.group(3) for alt in m.group(2).split(",")), command)
        if expanded == command:
            break
        command = expanded
    return command


def _segments_with_separators(command: str) -> list[tuple[list[str], str]]:
    """Split command into (tokens, separator that follows) pairs. A segment
    with no words is kept so its separator still counts."""
    parts = _SPLIT_KEEP_RE.split(command)
    out: list[tuple[list[str], str]] = []
    for idx in range(0, len(parts), 2):
        text = parts[idx].strip()
        sep = parts[idx + 1] if idx + 1 < len(parts) else ""
        toks: list[str] = []
        if text:
            try:
                toks = shlex.split(text, posix=True)
            except ValueError:
                # Unbalanced quotes: the naive splitter cut inside a quoted
                # string. Keep the words, without their stray quote marks.
                toks = [t.strip("'\"") for t in text.split()]
                toks = [t for t in toks if t]
        out.append((toks, sep))
    return out


def _cd_target(toks: list[str]) -> str | None:
    """For a cd/pushd segment, the directory it changes to ("~" for a bare cd);
    None when the segment is not a directory change or its target is unknown."""
    if toks and toks[0] == "builtin":
        toks = toks[1:]
    if not toks or toks[0] not in _CD_HEADS:
        return None
    args = [t for t in toks[1:] if not (t.startswith("-") and t != "-")]
    if not args:
        return "~"
    if args[0] == "-" or args[0].startswith("+") or "$" in args[0] or "`" in args[0]:
        # cd -, pushd +N, or a runtime-built target: the directory is unknown,
        # so later segments keep every directory seen so far.
        return None
    return args[0]


def _next_cwds(cwds: list[Path], target: str) -> list[Path]:
    out: list[Path] = []
    for base in cwds:
        try:
            out.append(_resolve(base, target))
        except (OSError, ValueError):
            continue
        matches, _ = _glob_paths(target, base)
        out.extend(matches)
    return out


def _add_cwds(pool: list[Path], extra: list[Path]) -> list[Path]:
    for c in extra:
        if c not in pool and len(pool) < _MAX_CWDS:
            pool.append(c)
    return pool


def _scan_bash(command: str, clients: Path, claude_dir: Path, workspace: Path, cwd: Path | list[Path],
               _depth: int = 0) -> str:
    """Check every segment of command, then recurse into $(...) / backtick
    substitutions (already exposed as their own segments by the paren/backtick
    splitter, and re-checked explicitly below for robustness) and into the string
    argument of bash -c / sh -c / zsh -c.

    A cd or pushd earlier in the command changes the directory later relative
    paths resolve against. Every segment is checked against each directory it
    could run in: right after `cd X &&` that is X alone; after any other
    separator (;, ||, |, &, a newline) the cd may have failed or run in a
    subshell, so the earlier directories stay possible too. When the command
    has grouping ((), {}, backticks), every directory seen stays possible."""
    if _depth > 8 or not command:
        return ""
    command = _expand_home_in_command(command)
    command = _PWD_BRACED_RE.sub("$PWD", command)
    command = _expand_braces(command)
    start = list(cwd) if isinstance(cwd, list) else [cwd]
    seen: list[Path] = list(start)
    current: list[Path] = list(start)
    grouped = any(c in command for c in _GROUPING_CHARS)
    for toks, sep in _segments_with_separators(command):
        for here in current:
            reason = _block_command(toks, clients, claude_dir, workspace, here) if toks else ""
            if reason:
                return reason
        for inner in _shell_c_strings(toks):
            reason = _scan_bash(inner, clients, claude_dir, workspace, list(current), _depth + 1)
            if reason:
                return reason
        target = _cd_target(toks)
        moved = _next_cwds(current, target) if target is not None else []
        _add_cwds(seen, moved)
        if moved and sep == "&&" and not grouped:
            current = list(dict.fromkeys(moved))[:_MAX_CWDS]
        elif sep == "&&" and not grouped:
            pass
        else:
            current = list(seen)
    # The segment splitter above cuts inside quotes, so a quoted `bash -c "..."`
    # string containing && or ; reaches the loop in pieces. Parse the whole
    # command once more, quote-aware, and scan each -c string intact.
    try:
        whole = shlex.split(command, posix=True)
    except ValueError:
        whole = []
    for inner in _shell_c_strings(whole):
        reason = _scan_bash(inner, clients, claude_dir, workspace, list(seen), _depth + 1)
        if reason:
            return reason
    for nested in _direct_substitutions(command):
        reason = _scan_bash(nested, clients, claude_dir, workspace, list(seen), _depth + 1)
        if reason:
            return reason
    return ""


_SHELL_C_FLAG_RE = re.compile(r"^-[a-zA-Z]*c[a-zA-Z]*$")


def _shell_c_strings(toks: list[str]) -> list[str]:
    """The command strings passed to bash/sh/zsh with -c (also -lc, -ec)."""
    found = []
    for i in range(len(toks) - 2):
        if _basename(toks[i]) in SHELL_HEADS and _SHELL_C_FLAG_RE.match(toks[i + 1]):
            found.append(toks[i + 2])
    return found


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


def _mcp_reaches_salesforce(tool_name: str) -> bool:
    """True when an MCP tool name (mcp__<server>__<tool>) indicates Salesforce
    org access, by its server or tool name. Unrelated MCP tools pass."""
    name = tool_name.casefold()
    if any(part in name for part in _MCP_SF_SUBSTRINGS):
        return True
    return any(_MCP_SF_TOKEN_RE.search(part) for part in name.split("__")[1:])


def decide(tool_name: str, tool_input: dict, workspace: Path, mode: str,
           cwd: Path | None = None) -> tuple[bool, str]:
    if mode != "build-only":
        return True, ""
    workspace = Path(os.path.realpath(str(workspace)))
    cwd = Path(os.path.realpath(str(cwd))) if cwd is not None else workspace
    clients = Path(os.path.realpath(str(workspace / "clients")))
    claude_dir = Path(os.path.realpath(str(workspace / ".claude")))
    if tool_name.startswith("mcp__"):
        if _mcp_reaches_salesforce(tool_name):
            return False, ("De-identified mode: this MCP tool looks like Salesforce org access. "
                           "Disable Salesforce MCP servers in a build-only workspace.")
        reason = _mcp_path_reason(tool_name, tool_input, clients, claude_dir, cwd)
        if reason:
            return False, f"De-identified mode: {reason}."
        return True, ""
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
        if tool_name != "Read" and (_is_within(target, _package_dir())
                                    or _TORQUE_INSTALL_RE.search(target.as_posix())):
            return False, ("De-identified mode: the installed Torque package enforces this mode; "
                           "only the owner changes it.")
    return True, ""


def _string_values(value: object, depth: int = 0):
    """Every string inside a tool_input (dict values and list items, nested)."""
    if depth > 8:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _string_values(item, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _string_values(item, depth + 1)


def _mcp_path_reason(tool_name: str, tool_input: dict, clients: Path, claude_dir: Path, cwd: Path) -> str:
    """Treat every single-line string argument of an MCP tool as a possible path
    (a file:// URI included). Any that lands in clients/, the hook configuration
    or the installed Torque package is blocked; a tree-walking tool (search,
    tree, find, ...) rooted at or above clients/ is blocked too."""
    recursive = any(marker in tool_name.casefold() for marker in _MCP_RECURSIVE_MARKERS)
    for raw in _string_values(tool_input):
        if not raw or "\n" in raw or len(raw) > 4096:
            continue
        candidate = raw[len("file://"):] if raw.casefold().startswith("file://") else raw
        try:
            target = _resolve(cwd, candidate)
        except (OSError, ValueError):
            continue
        if _is_within(target, clients) or (recursive and _reaches(target, clients)):
            return "this MCP tool call names client context. Disable file-reading MCP servers in a build-only workspace"
        if _targets_guarded_file(target.as_posix()) or _is_within(target, claude_dir):
            return "this MCP tool call names workspace.json or the hook configuration"
        if _is_within(target, _package_dir()) or _TORQUE_INSTALL_RE.search(target.as_posix()):
            return "this MCP tool call names the installed Torque package that enforces this mode"
    return ""


def _resolve_ai_access(value: object) -> str:
    """Only an explicit "full" means full. Everything else present (null, an
    empty string, a typo, wrong case, a non-string) fails closed to build-only.
    A missing key is handled by the caller and means full, the documented default."""
    if value == "full":
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


def _workspace_chain(start: Path) -> list[tuple[Path, str, bool]]:
    """Every workspace from start upward, nearest first, as (folder, mode,
    mode_known). A folder counts when it has a workspace.json, or a real
    workspace marker (_is_workspace_marker) without one. mode_known is False
    when a workspace.json could not be read or parsed as a JSON object, or
    when the marker has no readable workspace.json alongside it (the config
    may have been removed); both are build-only. The search stops before the
    user's home directory (exclusive): home itself, and anything above it, is
    never treated as or searched for a workspace, since unrelated per-user
    state (e.g. an older torque tooling directory) can live directly under home."""
    home = _cf(str(_home_dir()))
    chain: list[tuple[Path, str, bool]] = []
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
                chain.append((folder, "build-only", False))
                continue
            mode = _resolve_ai_access(data["ai_access"]) if "ai_access" in data else "full"
            chain.append((folder, mode, True))
        elif _is_workspace_marker(folder):
            chain.append((folder, "build-only", False))
    return chain


def _workspace_mode(start: Path) -> tuple[Path, str, bool]:
    """The governing workspace for start: the strictest wins. A nested
    workspace.json (even `{}` or an explicit "full") cannot downgrade a
    build-only workspace above it. Returns the nearest build-only workspace
    when there is one, else the nearest workspace, else (start, "full", True)."""
    chain = _workspace_chain(start)
    for entry in chain:
        if entry[1] == "build-only":
            return entry
    return chain[0] if chain else (start, "full", True)


# The command a Claude Code hook should run. If `torque.gate` cannot be imported
# by the hook's interpreter (Torque missing, a broken install, an edited gate),
# the excepthook exits 2 (block) instead of Python's default exit 1, which
# Claude Code treats as a non-blocking error. It contains no double quote and no
# percent sign, so it can be wrapped in double quotes for sh, bash and cmd.
HOOK_SHIM_CODE = ("import os,sys;sys.excepthook=lambda t,e,b:(print('De-identified mode: the gate "
                  "could not load ('+t.__name__+': '+str(e)+'); blocking to fail closed.',"
                  "file=sys.stderr,flush=True),os._exit(2));from torque.gate import main;sys.exit(main())")


def hook_command(python: str) -> str:
    """The hook command for a given interpreter path (use forward slashes on Windows)."""
    return f'"{python}" -c "{HOOK_SHIM_CODE}"'


def main() -> int:
    try:
        event = json.loads(sys.stdin.read())
        if not isinstance(event, dict):
            raise ValueError("hook input must be a JSON object")
        if "tool_name" not in event:
            raise ValueError("hook input has no tool_name")
        cwd = Path(os.path.realpath(str(event.get("cwd") or ".")))
        gated = [folder for folder, mode, _ in _workspace_chain(cwd) if mode == "build-only"]
        tool_input = event.get("tool_input")
        if tool_input is None:
            tool_input = {}
        if not isinstance(tool_input, dict):
            raise ValueError("tool_input must be an object")
        allowed, reason = True, ""
        # Every build-only workspace from cwd upward applies; the strictest wins.
        for root in gated:
            allowed, reason = decide(str(event.get("tool_name", "")), tool_input, root, "build-only", cwd)
            if not allowed:
                break
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
