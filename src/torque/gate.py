"""Build-only mode: keep an AI session away from client orgs and client context.

This is a best-effort guard for an assistant following normal tool use. It scans
recognized tool calls (a Claude Code PreToolUse hook) for shapes that would reach a
Salesforce org, read or write client context, or disable the guard itself. It is not
a sandbox: a runtime-constructed command, an arbitrary script, a network tool, or a
host that does not wire up the hook can still get through. See docs/ai-access.md.
"""
from __future__ import annotations
import codecs
import glob
import itertools
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.parse
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
                   ("code-analyzer", "rules"): {"--workspace", "-w", "--target", "-t"},
                   ("project", "convert"): {"--root-dir", "-r", "--rootdir", "--source-dir", "-p",
                                            "--sourcepath", "--metadata-dir"}}
# Flags that make a tree reader resolve components from the project's package
# directories (under the current directory), so "." is read too.
SF_PACKAGE_DIR_FLAGS = {("project", "convert"): {"--manifest", "-x", "--metadata", "-m"}}
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
              "MultiEdit": "file_path", "NotebookEdit": "notebook_path", "NotebookRead": "notebook_path",
              "LS": "path"}
READ_TOOLS = {"Read", "NotebookRead", "LS"}
# LSP operations that answer about one file. The others (workspaceSymbol,
# findReferences, goToImplementation, the call hierarchy) return results from the
# language server's whole index, which is rooted at the workspace and covers
# clients/.
LSP_FILE_OPERATIONS = {"documentSymbol", "hover", "goToDefinition"}
# Claude Code creates EnterWorktree worktrees here, and copies the gitignored
# files that .worktreeinclude names into each one.
WORKTREES_DIR = (".claude", "worktrees")
WORKTREE_INCLUDE = ".worktreeinclude"
# Tools that name no path and run no command, allowed as they are. Any other tool
# that carries a `command` string (Monitor, PowerShell, ...) is scanned like Bash,
# and a tool this list and the checks below do not recognise is blocked.
SAFE_TOOLS = {"TodoWrite", "TodoRead", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet",
              "Task", "Agent", "TaskOutput", "TaskStop", "BashOutput", "KillShell", "KillBash",
              "WebSearch", "WebFetch", "ExitPlanMode", "EnterPlanMode", "AskUserQuestion",
              "Skill", "SlashCommand", "ToolSearch", "ListMcpResourcesTool", "SendMessage",
              "ExitWorktree"}
# Tools whose string arguments are checked like an MCP tool's.
MCP_LIKE_TOOLS = {"ReadMcpResourceTool"}
# Argument names that hold a command string, in a host tool or an MCP tool (at any depth).
COMMAND_KEYS = ("command", "cmd", "script")
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
_DIFF_RECURSIVE_FLAG_RE = re.compile(r"^--recursive$|^-[a-zA-Z]*r[a-zA-Z]*$")
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
_CD_HEADS = {"cd", "pushd", "popd"}
_CD_PREFIXES = {"builtin", "command", "time", "noglob", "nocorrect"}
_MAX_CWDS = 32
# Commands that write, create or replace files, for the checks that only apply
# to a write (a planted torque package, the hook interpreter's binaries).
_WRITE_VERBS = DESTRUCTIVE_VERBS | {"ln", "tee", "install", "chmod", "chown", "touch", "dd", "patch",
                                    "mkdir", "rsync", "scp", "tar", "bsdtar", "gtar", "gnutar", "unzip", "ditto", "sed", "perl"}
# Files an interpreter runs or imports at startup when found next to it or on sys.path.
_STARTUP_NAMES = {"sitecustomize.py", "usercustomize.py", "torque.py"}
# Git Bash (MSYS) and Cygwin drive paths: /c/Users/..., /cygdrive/c/Users/...
_MSYS_DRIVE_RE = re.compile(r"^/(?:cygdrive/)?([A-Za-z])(?=/|$)")
# ANSI-C quoting ($'\x63') and locale quoting ($"...").
_ANSI_C_RE = re.compile(r"\$'((?:[^'\\]|\\.)*)'")
# A zsh glob group or qualifier inside a word, c(l)ients or notes(.), and a brace
# group with no comma, c{l..l}ients. $(...) and ${...} are not groups.
_WORD_GROUP_RE = re.compile(r"(?<=[^\s$<>=(|&;`'\"])(\([^()\s]*\)|\{[^{}\s,]*\})"
                            r"|(\([^()\s]*\)|\{[^{}\s,]*\})(?=[^\s)}|&;<>`'\"])")
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


def _native_path(raw: str, windows: bool | None = None, drives: str | None = None) -> str:
    """On Windows, turn a Git Bash or Cygwin drive path (/c/Users/..., or
    /cygdrive/c/Users/...) into the C:/Users/... form the filesystem uses. Claude
    Code runs Bash through Git Bash there, and `pwd` prints the MSYS form. Like
    Git Bash, only a drive that exists is mapped (drives: the letters to treat as
    existing, for tests); otherwise /w stays a rooted path on the current drive."""
    if windows is None:
        windows = os.name == "nt"
    if not windows:
        return raw
    m = _MSYS_DRIVE_RE.match(raw)
    if not m:
        return raw
    letter = m.group(1).upper()
    exists = letter in drives.upper() if drives is not None else os.path.isdir(f"{letter}:/")
    if not exists:
        return raw
    return f"{letter}:" + (raw[m.end():] or "/")


def _expand_home(raw: str) -> str:
    """Expand a leading ~ and any $HOME/${HOME} the same way a shell would, using
    the actual HOME so a real symlinked or nonstandard home still resolves."""
    raw = _native_path(raw)
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
    return t == g or t.startswith(_with_sep(g))


def _reaches(root: Path, guarded: Path) -> bool:
    """True when root is guarded itself, an ancestor of guarded (so a recursive
    operation rooted at root could reach it), or already inside it."""
    a, b = _cf(str(root)), _cf(str(guarded))
    return a == b or b.startswith(_with_sep(a)) or a.startswith(_with_sep(b))


def _with_sep(path: str) -> str:
    """path with one trailing separator. A filesystem root (/, C:\\) already ends
    in one, and adding another would make nothing look inside it."""
    return path if path.endswith(("/", "\\")) else path + os.sep


def _targets_guarded_file(text: str) -> bool:
    """True if text (a raw Bash token, or a resolved path) names workspace.json,
    a .claude/settings*.json hook config or .worktreeinclude, case-insensitively.
    Only the owner changes these; an AI session must not be able to switch the
    mode off, or list clients/ for copying into a new worktree."""
    folded = text.casefold()
    if "workspace.json" in folded or WORKTREE_INCLUDE in folded:
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


@lru_cache(maxsize=1)
def _interpreter_paths() -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """What this (the hook's) interpreter loads at startup, resolved, as (always
    guarded, guarded against writes). Always guarded: its site-packages
    directories, where a .pth file, sitecustomize.py or a torque/ folder would
    replace the gate, and a virtual environment's pyvenv.cfg. Guarded against
    writes: the interpreter binary and, in a virtual environment, its scripts
    directory, which may still be run."""
    import site
    import sysconfig
    always: list[str] = []
    for key in ("purelib", "platlib"):
        always.append(sysconfig.get_paths().get(key) or "")
    try:
        always.extend(site.getsitepackages())
    except AttributeError:
        pass
    try:
        always.append(site.getusersitepackages())
    except AttributeError:
        pass
    # site.getsitepackages() also lists the installation prefix itself on
    # Windows; only the site directories are guarded outright.
    always = [p for p in always if p and os.path.basename(os.path.normpath(p)).casefold()
              in ("site-packages", "dist-packages")]
    writes = [sys.executable]
    if sys.prefix != sys.base_prefix:
        always.append(os.path.join(sys.prefix, "pyvenv.cfg"))
        writes.append(sysconfig.get_paths().get("scripts") or "")
    resolve = lambda items: tuple(dict.fromkeys(Path(os.path.realpath(p)) for p in items if p))
    return resolve(always), resolve(writes)


def _in_interpreter(target: Path, write: bool) -> bool:
    always, writes = _interpreter_paths()
    return any(_is_within(target, p) for p in always) or (write and any(_is_within(target, p) for p in writes))


def _is_shadow_path(target: Path, workspace: Path) -> bool:
    """True for a file that could stand in for Torque or run at interpreter
    startup, anywhere in the workspace: a torque/ folder or anything in it,
    torque.py, sitecustomize.py, usercustomize.py or a .pth file."""
    t, w = _cf(str(target)), _cf(str(workspace))
    if not t.startswith(w + os.sep):
        return False
    parts = re.split(r"[/\\]", t[len(w) + 1:])
    name = parts[-1]
    return "torque" in parts or name in _STARTUP_NAMES or name.endswith(".pth")


def _token_operand(tok: str) -> str:
    """The part of a raw Bash token that can name a file: the target of an
    attached redirection (<file, 2>file), the value of --flag=value, or the
    value of a NAME=value assignment. A bare flag has none, except curl's
    attached `-d@file`. A leading @ or < on a value (curl's `@file` and
    `-F name=<file` forms) is dropped, since the rest is read as a file."""
    m = _REDIRECT_RE.match(tok)
    if m:
        return m.group("rest")
    if tok.startswith("-"):
        if "=" in tok:
            value = tok.split("=", 1)[1]
        elif "@" in tok and not tok.startswith("--"):
            value = tok.split("@", 1)[1]
        else:
            return ""
    elif _ASSIGN_RE.match(tok):
        value = tok.split("=", 1)[1]
    else:
        value = tok
    return value[1:] if value[:1] in ("@", "<") and len(value) > 1 else value


def _split_attached_redirections(toks: list[str]) -> list[str]:
    """Split a redirection glued to the word before it (cat<file, x>>file) into
    the word and the redirection, as the shell does. Quotes are already gone, so
    a quoted < or > is split too, which only adds words to check."""
    out: list[str] = []
    for tok in toks:
        if _REDIRECT_RE.match(tok) or "=" in tok.split("<", 1)[0].split(">", 1)[0]:
            out.append(tok)
            continue
        m = re.search(r"(?:\d+|&)?(?:<<<|<<-?|<>|<&|>&|>>|>\||<|>)", tok)
        if m and m.start() > 0 and m.end() < len(tok):
            out.extend([tok[:m.start()], tok[m.start():]])
        else:
            out.append(tok)
    return out


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
    if head in TAR_HEADS:
        return True
    if head in RECURSIVE_COPY_HEADS:
        return any(_COPY_RECURSIVE_FLAG_RE.match(t) for t in rest)
    if head == "diff":
        return any(_DIFF_RECURSIVE_FLAG_RE.match(t) for t in rest)
    return False


def _recursive_search_targets(head: str, rest: list[str]) -> list[str]:
    """The path arguments a recursive search tool will actually read, as best we
    can tell from its usual grammar. grep/rg/ag/ack/fd take PATTERN [PATH...], so
    their first non-flag argument is the search pattern, not a path; find/tree/ls
    take PATH[...] directly, so every non-flag argument is a candidate path."""
    value_flags = _SEARCH_VALUE_FLAGS.get(head, set())
    pattern_flags = _SEARCH_PATTERN_FLAGS.get(head, set())
    root_flags = _SEARCH_ROOT_FLAGS.get(head, set())
    no_pattern_flags = _SEARCH_NO_PATTERN_FLAGS.get(head, set())
    pattern_value_flags = _SEARCH_PATTERN_VALUE_FLAGS.get(head, set())
    # Short options that take a value: the rest of a group (-A2, -eERROR) or the next word.
    value_letters = {f[1] for f in value_flags | pattern_flags if len(f) == 2 and f[0] == "-"}
    pattern_letters = {f[1] for f in pattern_flags if len(f) == 2 and f[0] == "-"}
    getopt_long = head in GREP_HEADS
    non_flags: list[str] = []
    roots: list[str] = []
    explicit_pattern = False
    no_pattern = False
    j = 0
    while j < len(rest):
        tok = rest[j]
        name, eq, _ = tok.partition("=")
        if tok == "--":
            non_flags.extend(rest[j + 1:])
            break
        if name in root_flags:
            if eq:
                roots.append(tok.split("=", 1)[1])
            elif j + 1 < len(rest):
                roots.append(rest[j + 1])
                j += 1
        elif name in pattern_flags or (getopt_long and name.startswith("--")
                                       and (_is_long_flag(name, "--regexp", 5) or name == "--file")):
            # -e PATTERN, --regexp=PATTERN, and grep's abbreviations (--reg).
            explicit_pattern = True
            j += 0 if eq else 1
        elif name in no_pattern_flags:
            # rg --files, ack -f: list files; every word is a path.
            no_pattern = True
        elif name in value_flags:
            if name in pattern_value_flags:
                explicit_pattern = True
            j += 0 if eq else 1
        elif tok.startswith("-") and not tok.startswith("--") and len(tok) > 2:
            # A short-option group: -rn, -rneERROR, -uuu, -A2. The first letter that
            # takes a value takes the rest of the group, or else the next word.
            for index, letter in enumerate(tok[1:], start=1):
                if letter in value_letters:
                    if letter in pattern_letters:
                        explicit_pattern = True
                    if index == len(tok) - 1:
                        j += 1
                    break
        elif not tok.startswith("-"):
            non_flags.append(tok)
        j += 1
    if head in PATTERN_FIRST_HEADS and non_flags and not explicit_pattern and not no_pattern:
        non_flags = non_flags[1:]
    return roots + non_flags


# Options that take a value, per search tool, so the value is not read as the
# pattern or a path. Pattern options (-e, -f) mean every other word is a path.
_GREP_VALUE_FLAGS = {"-A", "-B", "-C", "-m", "-d", "-D", "--after-context", "--before-context", "--context",
                     "--max-count", "--directories", "--devices", "--include", "--exclude", "--exclude-dir",
                     "--exclude-from", "--label", "--binary-files", "--color", "--colour", "--group-separator"}
_SEARCH_VALUE_FLAGS = {
    "grep": _GREP_VALUE_FLAGS, "egrep": _GREP_VALUE_FLAGS, "fgrep": _GREP_VALUE_FLAGS,
    "rg": {"-A", "-B", "-C", "-m", "-g", "-t", "-T", "-E", "-M", "-d", "-j", "-r", "--after-context",
           "--before-context", "--context", "--max-count", "--glob", "--iglob", "--type", "--type-not",
           "--type-add", "--type-clear", "--encoding", "--max-columns", "--max-depth", "--maxdepth",
           "--max-filesize", "--threads", "--replace", "--pre", "--pre-glob", "--sort", "--sortr",
           "--colors", "--color", "--context-separator", "--field-match-separator",
           "--field-context-separator", "--path-separator", "--ignore-file", "--dfa-size-limit",
           "--regex-size-limit", "--engine", "--hostname-bin", "--hyperlink-format", "--generate"},
    "ag": {"-A", "-B", "-C", "-m", "-G", "-g", "-p", "--after", "--before", "--context", "--max-count",
           "--file-search-regex", "--ignore", "--ignore-dir", "--depth", "--pager", "--path-to-ignore",
           "--workers", "--color-line-number", "--color-match", "--color-path"},
    "ack": {"-A", "-B", "-C", "-m", "-g", "--after-context", "--before-context", "--context", "--max-count",
            "--type", "--type-set", "--type-add", "--type-del", "--ignore-dir", "--noignore-dir",
            "--ignore-file", "--output", "--pager", "--color-filename", "--color-match", "--color-lineno"},
    "fd": {"-e", "-t", "-E", "-d", "-S", "-j", "-x", "-X", "-c", "-o", "--extension", "--type", "--exclude",
           "--max-depth", "--min-depth", "--exact-depth", "--size", "--threads", "--exec", "--exec-batch",
           "--color", "--owner", "--changed-within", "--changed-before", "--ignore-file", "--max-results",
           "--path-separator", "--batch-size", "--format"},
}
_SEARCH_PATTERN_FLAGS = {
    "grep": {"-e", "-f", "--regexp", "--file"}, "egrep": {"-e", "-f", "--regexp", "--file"},
    "fgrep": {"-e", "-f", "--regexp", "--file"}, "rg": {"-e", "-f", "--regexp", "--file"},
    "ack": {"--match"},
}
# Options naming a directory to search, as a root.
_SEARCH_ROOT_FLAGS = {"fd": {"--search-path", "--base-directory"}}
# Modes that take no pattern, so every word is a path.
_SEARCH_NO_PATTERN_FLAGS = {"rg": {"--files", "--type-list"}, "ack": {"-f"}}
# Value options whose value is the pattern (file-name regex modes).
_SEARCH_PATTERN_VALUE_FLAGS = {"ag": {"-g"}, "ack": {"-g"}}
# tar's short options that take a value, and its --directory option.
_TAR_VALUE_LETTERS = frozenset("bCfFgHKLNTVX")
TAR_HEADS = {"tar", "bsdtar", "gtar", "gnutar"}
_TAR_MODE_RE = re.compile(r"^[A-Za-z]*[ctxruAd][A-Za-z]*$")


def _tar_operands(args: list[str], cwd: Path) -> list[tuple[str, Path]] | None:
    """tar's operands, each with the directory it is read from. -C DIR, -CDIR and
    --directory=DIR apply, in order, to the operands after them; each one is
    relative to the one before. None when a directory is only known at run time."""
    if args and not args[0].startswith("-") and _TAR_MODE_RE.match(args[0]):
        # Old-style keys (tar cCf .. - .): each key letter that takes a value takes
        # the next word, in order, not the rest of the bundle.
        expanded: list[str] = []
        words = iter(args[1:])
        for letter in args[0]:
            expanded.append("-" + letter)
            if letter in _TAR_VALUE_LETTERS:
                word = next(words, None)
                if word is not None:
                    expanded.append(word)
        args = expanded + list(words)
    base = cwd
    out: list[tuple[str, Path]] = []
    j = 0
    while j < len(args):
        tok = args[j]
        if tok == "--":
            out.extend((t, base) for t in args[j + 1:])
            break
        value, is_dir = None, False
        if tok.startswith("--"):
            name, eq, val = tok.partition("=")
            if _is_long_flag(name, "--directory", 5):
                is_dir = True
                if eq:
                    value = val
                elif j + 1 < len(args):
                    value = args[j + 1]
                    j += 1
        elif tok.startswith("-") and len(tok) > 1:
            for index, letter in enumerate(tok[1:], start=1):
                if letter in _TAR_VALUE_LETTERS:
                    value = tok[index + 1:]
                    if not value and j + 1 < len(args):
                        value = args[j + 1]
                        j += 1
                    is_dir = letter == "C"
                    break
        else:
            out.append((tok, base))
        if value is not None:
            if is_dir:
                if not value or "$" in value or "`" in value:
                    return None
                try:
                    base = _resolve(base, value)
                except (OSError, ValueError):
                    return None
            else:
                out.append((value, base))
        j += 1
    return out or [(".", base)]


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
    """A local sf command that reads a directory tree (code-analyzer, project
    convert) must not be rooted at or above clients/. Its root defaults to the
    current directory."""
    head2 = tuple(t for t in rest[:2] if not t.startswith("-"))
    flags = SF_TREE_READERS.get(head2[:2])
    if flags is None:
        return False
    roots = _flag_values(rest, flags) or ["."]
    if _flag_values(rest, SF_PACKAGE_DIR_FLAGS.get(head2[:2], set())) is not None:
        roots.append(".")
    return any(_root_reaches(raw, clients, cwd) for raw in roots)


def _is_long_flag(tok: str, flag: str, shortest: int) -> bool:
    """True when tok is flag or an abbreviation git would accept for it (any
    prefix at least `shortest` characters long; git rejects an ambiguous one)."""
    name = tok.split("=", 1)[0]
    return len(name) >= shortest and flag.startswith(name)


def _reads_untracked(tok: str) -> bool:
    return _is_long_flag(tok, "--untracked", 3) or _is_long_flag(tok, "--no-index", 6)


def _git_reaches(rest: list[str], clients: Path, cwd: Path) -> bool:
    """`git grep --untracked`, `git grep --no-index` and `git diff --no-index`
    read files Git does not track, and clients/ is git-ignored, so none of them
    may be rooted at or above clients/. Git accepts any unambiguous prefix of a
    long option (--untr, --no-ind), so prefixes count too. Plain `git grep` and
    `git diff` read tracked content only."""
    try:
        base, _, sub, args = _git_parse(rest, cwd)
    except (OSError, ValueError):
        return True
    if sub not in ("grep", "diff"):
        return False
    if sub == "diff":
        if not any(_is_long_flag(t, "--no-index", 6) for t in args):
            return False
        paths = args[args.index("--") + 1:] if "--" in args else [t for t in args if not t.startswith("-")]
        return any(_root_reaches(raw, clients, base) for raw in paths)
    if not any(_reads_untracked(t) for t in args):
        return False
    # `--` ends the options. Without -e/-f, the pattern is the first word, before
    # or (git grep -- PATTERN) just after it; every other word is a path.
    pre, post = (args[:args.index("--")], args[args.index("--") + 1:]) if "--" in args else (args, [])
    non_flags: list[str] = []
    explicit_pattern = False
    k = 0
    while k < len(pre):
        tok = pre[k]
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
    if explicit_pattern:
        paths = non_flags + post
    elif non_flags:
        paths = non_flags[1:] + post
    else:
        paths = post[1:]
    return any(_root_reaches(raw, clients, base) for raw in (paths or ["."]))


_GIT_GREP_PATTERN_FLAGS = {"-e", "-f", "--regexp", "--file"}
_GIT_GREP_VALUE_FLAGS = {"-A", "-B", "-C", "--after-context", "--before-context", "--context",
                         "-m", "--max-count", "--max-depth", "--threads", "-O", "--open-files-in-pager"}
_STASH_ACTIONS = {"push", "save", "show", "list", "apply", "pop", "drop", "branch", "clear", "create", "store"}
# A stash's untracked files are its third parent: stash^3, stash@{0}^3, refs/stash^3.
_STASH_UNTRACKED_REF_RE = re.compile(r"(?<![A-Za-z0-9_.-])(?:refs/)?stash(?:@\{[^}]*\}|~\d*)*\^3")
GIT_STAGE_REASON = ("git add with -f, or of client files git does not ignore, at or above client context, the "
                    ".claude hook configuration or the hook's environment would copy them into git. Name "
                    "paths such as project/")
GIT_WIPE_REASON = ("git clean, or git stash of untracked files, run at or above client context, the .claude "
                   "hook configuration or the hook's environment can delete them or copy them into git. "
                   "Use git clean -n to preview, or name paths such as project/")


def _git_parse(rest: list[str], cwd: Path) -> tuple[Path, list[Path], str | None, list[str]]:
    """Split the words after `git` into (directory after any -C, work trees named
    by --work-tree or --git-dir, subcommand, its arguments). Raises when a -C
    or work-tree path cannot be resolved."""
    base = cwd
    trees: list[Path] = []
    j = 0
    while j < len(rest):
        tok = rest[j]
        if tok == "-C" and j + 1 < len(rest):
            base = _resolve(base, rest[j + 1])
            j += 2
            continue
        name, eq, value = tok.partition("=")
        if name in ("--work-tree", "--git-dir"):
            if not eq:
                value = rest[j + 1] if j + 1 < len(rest) else ""
                j += 1
            if value:
                path = _resolve(base, value)
                trees.append(path if name == "--work-tree" else path.parent)
            j += 1
            continue
        if tok in ("-c", "--namespace") and j + 1 < len(rest):
            j += 2
            continue
        if tok.startswith("-"):
            j += 1
            continue
        break
    sub = rest[j] if j < len(rest) else None
    return base, trees, sub, rest[j + 1:]


# git subcommands that print file contents or names from the index, history or
# stashes. They are blocked while the index or a stash holds client files.
_GIT_CONTENT_READERS = {"show", "diff", "log", "cat-file", "grep", "archive", "blame", "annotate", "format-patch",
                        "whatchanged", "reflog", "ls-tree", "ls-files", "rev-list", "difftool", "range-diff",
                        "bundle", "fast-export", "stash", "shortlog", "cherry", "notes"}
_CLIENT_SPEC = ":(icase)clients"


def _git_add_roots(args: list[str]) -> tuple[bool, list[str] | None]:
    """For `git add` arguments: (forced, the pathspecs it stages). A pathspec file,
    or -A/--all with no pathspec (the whole tree), give None: the repository top."""
    options = args[:args.index("--")] if "--" in args else args
    forced = any(_is_long_flag(t, "--force", 5) if t.startswith("--") else _short_flag_has(t, "f", "") for t in options)
    if any(t.startswith("--pathspec-from-file") for t in options):
        return forced, None
    specs = _git_pathspecs(args, {"--chmod"})
    whole = any(t.startswith("--") and (_is_long_flag(t, "--all", 4) or _is_long_flag(t, "--no-ignore-removal", 6))
                or _short_flag_has(t, "A", "") for t in options)
    if not specs and whole:
        return forced, None
    return forced, specs


def _git_holds_clients(base: Path) -> bool:
    """True when the index, or the untracked part of any stash, holds a file under
    clients/, or when git cannot say. Outside a repository (or without git) there
    is nothing for git to print."""
    if not (base / ".git").exists() and _git_toplevel(base) is None:
        return False
    staged = _git_output(base, ["ls-files", "--", _CLIENT_SPEC])
    if staged is None or staged.strip():
        return True
    stashes = _git_output(base, ["log", "-g", "--format=%H", "refs/stash"])
    if stashes is None:
        # No stash at all (the ref is missing) is the ordinary case.
        return _git_output(base, ["rev-parse", "-q", "--verify", "refs/stash"]) is not None
    for sha in stashes.split():
        # ls-tree takes no icase pathspec; the untracked tree is filtered here.
        names = _git_output(base, ["ls-tree", "-r", "--name-only", f"{sha}^3"]) or ""
        if any(name.casefold().startswith("clients/") for name in names.splitlines()):
            return True
    return False


def _git_stage_reason(rest: list[str], clients: Path, claude_dir: Path, cwd: Path) -> str:
    """`git add -f` rooted at or above clients/, .claude/ or the hook's environment
    stages ignored files there; a plain `git add` does the same to client files git
    does not ignore. And while the index or a stash holds client files, commands
    that print index, history or stash content (git diff --cached, git show :path,
    git log -p, git stash show) are blocked."""
    try:
        base, trees, sub, args = _git_parse(rest, cwd)
    except (OSError, ValueError):
        return "" if not any(t in ("add", *_GIT_CONTENT_READERS) for t in rest) else GIT_STAGE_REASON
    if sub == "add":
        forced, specs = _git_add_roots(args)
        protected = _git_protected(clients, claude_dir) if forced else [clients]
        if specs is None:
            reaches = bool(trees) or _toplevel_reaches(base, protected)
        elif not specs:
            return ""
        else:
            reaches = bool(trees) or _pathspecs_reach(specs, base, protected)
        if not reaches:
            return ""
        if forced:
            return GIT_STAGE_REASON
        # Pathspecs are relative to the directory git runs in: ask from the workspace.
        loose = _git_output(clients.parent, ["ls-files", "-o", "--exclude-standard", "--", _CLIENT_SPEC])
        return GIT_STAGE_REASON if loose is None or loose.strip() else ""
    if sub in _GIT_CONTENT_READERS and _git_holds_clients(clients.parent):
        return ("git holds client files (in the index or a stash), so commands that print index, history or "
                "stash content are blocked. Ask the owner to run git rm -r --cached clients or drop the stash")
    return ""


def _git_output(base: Path, args: list[str]) -> str | None:
    """Run a read-only git query in base; None when git fails or is missing. The
    workspace's own git settings cannot start an fsmonitor command here."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        done = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(base), *args],
                              capture_output=True, text=True, timeout=15, env=env,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return done.stdout if done.returncode == 0 else None


def _git_toplevel(base: Path) -> Path | None:
    out = _git_output(base, ["rev-parse", "--show-toplevel"])
    if not out or not out.strip():
        return None
    return Path(os.path.realpath(out.strip()))


def _git_protected(clients: Path, claude_dir: Path) -> list[Path]:
    """What git clean or an untracked stash must not reach: clients/, .claude/,
    the installed Torque package and the hook interpreter's environment."""
    always, writes = _interpreter_paths()
    return [clients, claude_dir, _package_dir(), *always, *writes]


def _git_pathspecs(args: list[str], value_flags: set[str]) -> list[str]:
    """The pathspecs among a git subcommand's arguments: every word after `--`,
    and before it every word that is not a flag or a flag's value."""
    out: list[str] = []
    j = 0
    while j < len(args):
        tok = args[j]
        if tok == "--":
            return out + args[j + 1:]
        if tok in value_flags:
            j += 2
            continue
        if not tok.startswith("-"):
            out.append(tok)
        j += 1
    return out


def _pathspecs_reach(raws: list[str], base: Path, protected: list[Path]) -> bool:
    """True when a pathspec, read from base, reaches a protected path. Git matches
    a wildcard across directories, so a pathspec with one is rooted at its fixed
    prefix. A magic pathspec (`:/` is the repository's top) is not interpreted."""
    for raw in raws:
        if raw.startswith(":"):
            return True
        root = _resolve(base, _glob_fixed_prefix(raw) if _has_glob_char(raw) else raw)
        if any(_reaches(root, p) for p in protected):
            return True
    return False


def _toplevel_reaches(base: Path, protected: list[Path]) -> bool:
    """True when the repository base belongs to reaches a protected path, or when
    git cannot say which repository that is."""
    top = _git_toplevel(base)
    return top is None or any(_reaches(top, p) for p in protected)


def _short_flag_has(tok: str, letters: str, value_letters: str = "") -> bool:
    """True when a short-option group (-fdx) holds one of letters, before any
    letter that takes the rest of the group as its value."""
    if not tok.startswith("-") or tok.startswith("--") or len(tok) < 2:
        return False
    group = tok[1:]
    for index, ch in enumerate(group):
        if ch in value_letters:
            group = group[:index]
            break
    return any(ch in group for ch in letters)


def _stash_reads_untracked(args: list[str], action: str) -> bool:
    for tok in args:
        if tok == "--":
            break
        if tok.startswith("--"):
            if _is_long_flag(tok, "--include-untracked", 3) or _is_long_flag(tok, "--only-untracked", 4):
                return True
            if action != "show" and _is_long_flag(tok, "--all", 3):
                return True
        elif _short_flag_has(tok, "u" if action == "show" else "ua", "m"):
            return True
    return False


def _git_wipe_reaches(rest: list[str], words: list[str], clients: Path, claude_dir: Path, cwd: Path) -> bool:
    """`git clean` (other than a dry run) and `git stash` of untracked files (-u,
    --include-untracked, -a, --all) delete or copy files git does not track,
    clients/, .claude/ and a virtual environment among them. Either is blocked
    when run at or above those, or the hook's environment. `git stash show -u`
    and a stash's untracked parent (stash^3) read such a copy back. git clean
    works from the current directory down; a stash covers its whole repository
    unless pathspecs narrow it."""
    try:
        base, trees, sub, args = _git_parse(rest, cwd)
        for word in words:
            name, eq, value = word.partition("=")
            if eq and value and name in ("GIT_WORK_TREE", "GIT_DIR"):
                path = _resolve(cwd, value)
                trees.append(path if name == "GIT_WORK_TREE" else path.parent)
    except (OSError, ValueError):
        return any(t in ("clean", "stash") for t in rest) or any(_STASH_UNTRACKED_REF_RE.search(t) for t in rest)
    protected = _git_protected(clients, claude_dir)
    tree_reaches = any(_reaches(t, p) for t in trees for p in protected)
    if any(_STASH_UNTRACKED_REF_RE.search(t) for t in args):
        return tree_reaches or _toplevel_reaches(base, protected)
    if sub == "clean":
        options = args[:args.index("--")] if "--" in args else args
        if any(_is_long_flag(t, "--dry-run", 3) if t.startswith("--") else _short_flag_has(t, "n", "e")
               for t in options):
            return False
        roots = _git_pathspecs(args, {"-e", "--exclude"}) or ["."]
        return tree_reaches or _pathspecs_reach(roots, base, protected)
    if sub != "stash":
        return False
    action = args[0] if args and args[0] in _STASH_ACTIONS else "push"
    action_args = args[1:] if args and args[0] in _STASH_ACTIONS else args
    if action not in ("push", "save", "show") or not _stash_reads_untracked(action_args, action):
        return False
    if tree_reaches:
        return True
    specs: list[str] = []
    if action == "push" and not any(t.startswith("--pathspec-from-file") for t in action_args):
        specs = _git_pathspecs(action_args, {"-m", "--message"})
    if specs:
        return _pathspecs_reach(specs, base, protected)
    return _toplevel_reaches(base, protected)


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
    if sub == "doctor" and any(_is_long_flag(t, "--client", 3) for t in args[1:]):
        # The CLI rejects abbreviated options, but an older install may not.
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
            yield _split_attached_redirections(toks)


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
        elif _basename(tok) == "git" and _git_reaches(rest, clients, cwd):
            return "git grep or git diff over untracked files can read client context." + NARROW_PATH_HINT
        elif _basename(tok) == "git" and _git_wipe_reaches(rest, words, clients, claude_dir, cwd):
            return GIT_WIPE_REASON
        elif _basename(tok) == "git" and _git_stage_reason(rest, clients, claude_dir, cwd):
            return _git_stage_reason(rest, clients, claude_dir, cwd)
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
        elif _basename(tok) in TAR_HEADS:
            operands = _tar_operands(rest, cwd)
            if operands is None or any(_root_reaches(raw, clients, base) for raw, base in operands):
                return "this command can search client context recursively." + NARROW_PATH_HINT
        elif _is_recursive_search(tok, rest):
            if _recursive_search_reaches(_basename(tok), rest, clients, cwd):
                return "this command can search client context recursively." + NARROW_PATH_HINT
    chdir = _env_chdir(toks)
    if chdir is not None:
        # env -C DIR / --chdir=DIR runs the rest of the command in DIR.
        target, rest_toks = chdir
        if not target or "$" in target or "`" in target:
            bases = _widened_cwds([cwd], [cwd], workspace)
        else:
            bases = [cwd, *_next_cwds([cwd], target)]
        for base in dict.fromkeys(bases):
            reason = _block_command(rest_toks, clients, claude_dir, workspace, base)
            if reason:
                return reason
        return ""
    for tok in _removal_operands(words):
        paths, _ = _token_paths(tok, cwd)
        if paths:
            if any(_holds_gate(p) for p in paths):
                return ("this command would remove or replace the environment that holds the Torque gate "
                        "or the hook's Python")
    writes = (any(_basename(t) in _WRITE_VERBS for t in words)
              or any(re.match(r"^(\d+|&)?>", t) for t in toks))
    head_index = toks.index(words[0]) if words else -1
    for index, tok in enumerate(toks):
        reason = _token_reason(tok, clients, claude_dir, workspace, cwd, writes and index != head_index)
        if reason:
            if _has_glob_char(_token_operand(tok) or tok):
                return (f"the glob {tok} expands to paths that include client context, the hook "
                        "configuration or the Torque installation." + NARROW_PATH_HINT)
            return reason
    return ""


def _env_chdir(toks: list[str]) -> tuple[str, list[str]] | None:
    """For `env ... -C DIR` / `--chdir DIR` / `--chdir=DIR`, the directory and
    the tokens with the option removed (DIR stays, as an ordinary word)."""
    for i, tok in enumerate(toks):
        if _basename(tok) != "env":
            continue
        j = i + 1
        while j < len(toks) and (toks[j].startswith("-") or _ASSIGN_RE.match(toks[j])):
            opt = toks[j]
            if opt in ("-C", "--chdir"):
                target = toks[j + 1] if j + 1 < len(toks) else ""
                return target, toks[:j] + toks[j + 1:]
            if opt.startswith("--chdir="):
                return opt.split("=", 1)[1], toks[:j] + [opt.split("=", 1)[1]] + toks[j + 1:]
            if opt.startswith("-C") and not opt.startswith("--"):
                return opt[2:], toks[:j] + [opt[2:]] + toks[j + 1:]
            j += 1
    return None


# Commands that delete, move away, lock or recreate what their arguments name.
_REMOVE_VERBS = {"rm", "rmdir", "unlink", "shred", "truncate", "mv", "chmod", "chown", "virtualenv"}


def _removal_operands(words: list[str]) -> list[str]:
    """The words a removing command acts on (for mv, its sources): rm, rmdir,
    unlink, shred, truncate, mv, chmod, chown, find -delete/-exec, and
    recreating a virtual environment (python -m venv, virtualenv, uv venv)."""
    for i, tok in enumerate(words):
        name = _basename(tok)
        args = [t for t in words[i + 1:] if not t.startswith("-")]
        if name == "mv":
            return args[:-1]
        if name in _REMOVE_VERBS:
            return args
        if name == "find" and any(t in ("-delete", "-exec", "-execdir") for t in words[i + 1:]):
            return args
        if name == "uv" and "venv" in words[i + 1:]:
            return args
        if PY_LAUNCHER_RE.match(name):
            module, after = _python_module(words[i + 1:])
            if module in ("venv", "virtualenv"):
                return [t for t in words[i + 1 + after:] if not t.startswith("-")]
    return []


def _holds_gate(path: Path) -> bool:
    """True when path is the installed torque package, the hook interpreter's
    site-packages, scripts folder or binary, or a folder containing any of them."""
    always, writes = _interpreter_paths()
    return any(_is_within(protected, path) for protected in (_package_dir(), *always, *writes))


def _token_reason(tok: str, clients: Path, claude_dir: Path, workspace: Path, cwd: Path, write: bool) -> str:
    """Why one Bash token is blocked, or "". write is True when the command
    writes files and tok is not the command being run."""
    if _token_names_guarded_file(tok, cwd):
        return "this command targets workspace.json or the hook configuration"
    if _token_targets_claude_dir(tok, claude_dir, cwd):
        return "this command targets the .claude hook configuration directory"
    if _token_targets_package(tok, cwd):
        return "this command targets the installed Torque package that enforces this mode"
    if _token_is_client_path(tok, clients, cwd):
        return "this command reaches client context"
    paths, _ = _token_paths(tok, cwd)
    if any(_in_interpreter(p, write) for p in paths):
        return "this command targets the Python installation that runs the build-only mode hook"
    if write and any(_is_shadow_path(p, workspace) for p in paths):
        return ("this command writes a torque package, torque.py, a .pth file or a "
                "sitecustomize/usercustomize module, which could replace the hook's gate")
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
        out.append((_split_attached_redirections(toks), sep))
    return out


def _cd_target(toks: list[str]) -> str | None:
    """For a cd/pushd/popd segment (also after `builtin`, `command` or NAME=value
    prefixes), the directory it changes to ("~" for a bare cd), or "" when that
    directory cannot be known here. None when the segment is not a directory change."""
    j = 0
    while j < len(toks) and (toks[j] in _CD_PREFIXES or _ASSIGN_RE.match(toks[j])):
        j += 1
    words = _without_redirections(toks[j:])
    if not words or words[0] not in _CD_HEADS:
        return None
    args = [t for t in words[1:] if not (t.startswith("-") and t != "-")]
    if words[0] == "popd" or (words[0] == "pushd" and not args):
        # popd, and a bare pushd (which swaps the top two stack entries).
        return ""
    if not args:
        return "~"
    target = args[0]
    if target == "-" or target.startswith("+") or "$" in target or "`" in target:
        # cd -, pushd +N, or a runtime-built target.
        return ""
    if target.startswith("~") and not (target == "~" or target.startswith("~/")):
        # ~- (OLDPWD), ~+ (PWD), ~user and zsh's ~N directory stack entries.
        return ""
    return target


def _widened_cwds(seen: list[Path], start: list[Path], workspace: Path) -> list[Path]:
    """Every directory the shell could be in after a cd to an unknown target:
    each one seen so far, each starting directory and its parents (which include
    the workspace root and every directory between), and the workspace root's
    own parents."""
    out = list(seen)
    for base in [*start, workspace]:
        out.extend([base, *base.parents])
    return list(dict.fromkeys(out))


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

    A cd, pushd or popd earlier in the command changes the directory later
    relative paths resolve against. Every segment is checked against each
    directory it could run in: right after `cd X &&` that is X alone; after any
    other separator (;, ||, |, &, a newline) the cd may have failed or run in a
    subshell, so the earlier directories stay possible too. When the command
    has grouping ((), {}, backticks), every directory seen stays possible. After
    a directory change whose target cannot be known here (cd -, cd ~-, popd,
    cd "$VAR", cd "$(...)"), the rest of the command is checked from every
    directory seen, the starting directories and all of their parents."""
    if _depth > 8 or not command:
        return ""
    command = _expand_home_in_command(command)
    command = _decode_ansi_c(command)
    command = _PWD_BRACED_RE.sub("$PWD", command)
    command = _expand_braces(command)
    start = list(cwd) if isinstance(cwd, list) else [cwd]
    if _STASH_UNTRACKED_REF_RE.search(command) and re.search(r"(?<![A-Za-z0-9_.-])git(?![A-Za-z0-9_-])", command):
        # The segment splitter below cuts stash@{0}^3 at its braces, so a stash's
        # untracked parent is looked for in the whole command.
        protected = _git_protected(clients, claude_dir)
        if any(_toplevel_reaches(base, protected) for base in start):
            return GIT_WIPE_REASON
    globbed = _WORD_GROUP_RE.sub("*", command)
    if globbed != command:
        # zsh glob groups and qualifiers (c(l)ients, notes(.)) and comma-less
        # brace groups (c{l..l}ients) may match protected paths: check the
        # command again with each group read as a wildcard.
        reason = _scan_bash(globbed, clients, claude_dir, workspace, cwd, _depth + 1)
        if reason:
            return reason
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
        if target is not None and sep in ("`", "("):
            # cd `...` or cd $(...): the splitter cut the target off.
            target = ""
        if target == "":
            seen = _widened_cwds(seen, start, workspace)
            current = list(seen)
            continue
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


def _decode_ansi_c(command: str) -> str:
    """Decode bash/zsh ANSI-C quoting ($'\\x63lients' is 'clients') into an
    ordinary single-quoted word, and drop the $ of locale quoting ($"...")."""
    def decode(m: re.Match) -> str:
        try:
            text = codecs.decode(m.group(1).encode("latin-1", "backslashreplace"), "unicode_escape")
        except (UnicodeError, ValueError):
            return m.group(0)
        return shlex.quote(text)
    return _ANSI_C_RE.sub(decode, command).replace('$"', '"')


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


def _worktree_copies(workspace: Path) -> list[Path]:
    """The worktrees under the workspace's .claude/worktrees/, resolved. Each may
    hold a copy of clients/ (tracked client files, or files .worktreeinclude
    names), so each is checked as a workspace of its own."""
    folder = workspace.joinpath(*WORKTREES_DIR)
    try:
        entries = sorted(p for p in folder.iterdir() if p.is_dir())
    except OSError:
        return []
    copies = [Path(os.path.realpath(str(p))) for p in entries]
    return [c for c in dict.fromkeys(copies) if _cf(str(c)) != _cf(str(workspace))]


def decide(tool_name: str, tool_input: dict, workspace: Path, mode: str,
           cwd: Path | None = None) -> tuple[bool, str]:
    if mode != "build-only":
        return True, ""
    workspace = Path(os.path.realpath(str(workspace)))
    cwd = Path(os.path.realpath(str(cwd))) if cwd is not None else workspace
    allowed, reason = _decide_root(tool_name, tool_input, workspace, cwd, copy=False)
    if not allowed:
        return allowed, reason
    for copy in _worktree_copies(workspace):
        allowed, reason = _decide_root(tool_name, tool_input, copy, cwd, copy=True)
        if not allowed:
            return allowed, reason
    return True, ""


def _decide_root(tool_name: str, tool_input: dict, workspace: Path, cwd: Path, copy: bool) -> tuple[bool, str]:
    """decide() for one root: the workspace, or (copy=True) a worktree copy of it
    under .claude/worktrees/, whose clients/ is guarded the same way."""
    clients = Path(os.path.realpath(str(workspace / "clients")))
    claude_dir = Path(os.path.realpath(str(workspace / ".claude")))
    if tool_name.startswith("mcp__") or tool_name in MCP_LIKE_TOOLS:
        # An MCP tool that runs a command (a shell or process server) gets the
        # Bash scan on its command strings before its path arguments are checked.
        for text in _command_strings(tool_input):
            reason = _scan_bash(text, clients, claude_dir, workspace, cwd)
            if reason:
                return False, f"Build-only mode: {reason}. Run it yourself outside the AI session."
    if tool_name.startswith("mcp__"):
        if _mcp_reaches_salesforce(tool_name):
            return False, ("Build-only mode: this MCP tool looks like Salesforce org access. "
                           "Disable Salesforce MCP servers in a build-only workspace.")
        reason = _mcp_path_reason(tool_name, tool_input, clients, claude_dir, workspace, cwd)
        if reason:
            return False, f"Build-only mode: {reason}."
        return True, ""
    if tool_name == "LSP":
        reason = _lsp_reason(tool_input, clients, claude_dir, workspace, cwd)
        if reason:
            return False, f"Build-only mode: {reason}."
        return True, ""
    if tool_name == "EnterWorktree":
        # A worktree copy's own pass has nothing to add: entering is checked
        # against the workspace that holds .claude/worktrees/.
        reason = "" if copy else _enter_worktree_reason(tool_input, workspace, cwd)
        if reason:
            return False, f"Build-only mode: {reason}."
        return True, ""
    command = tool_input.get("command")
    if tool_name not in SAFE_TOOLS and tool_name != "Bash" and not isinstance(command, str):
        command = next((tool_input[k] for k in COMMAND_KEYS if isinstance(tool_input.get(k), str)), None)
    if tool_name == "Bash" or (tool_name not in SAFE_TOOLS and isinstance(command, str)):
        # Bash, and any other tool that runs a command string: Monitor runs in
        # the Bash tool's shell; PowerShell gets the same best-effort path scan,
        # with its backslash separators read as slashes.
        text = str(command or "")
        if "powershell" in tool_name.casefold():
            text = text.replace("\\", "/")
        reason = _scan_bash(text, clients, claude_dir, workspace, cwd)
        if reason:
            return False, f"Build-only mode: {reason}. Run it yourself outside the AI session."
        return True, ""
    if tool_name in ("Grep", "Glob"):
        raw_path = tool_input.get("path")
        root = _resolve(cwd, str(raw_path)) if raw_path else cwd
        if _reaches(root, clients):
            hint = NARROW_PATH_HINT if raw_path else (
                f" With no path, {tool_name} searches the current directory, which contains clients/. "
                "Pass a path, such as project/ or src/.")
            return False, "Build-only mode: client context stays out of the AI session." + hint
        pattern = str(tool_input.get("pattern") or "")
        glob_field = str(tool_input.get("glob") or "")
        mentions_clients = "clients" in pattern.casefold() or "clients" in glob_field.casefold()
        if mentions_clients and _reaches(cwd, clients):
            return False, "Build-only mode: client context stays out of the AI session." + NARROW_PATH_HINT
        return True, ""
    key = PATH_TOOLS.get(tool_name)
    if key:
        if not tool_input.get(key):
            return True, ""
        target = _resolve(cwd, str(tool_input[key]))
        write = tool_name not in READ_TOOLS
        if _is_within(target, clients):
            return False, "Build-only mode: client context stays out of the AI session."
        if write and _targets_guarded_file(target.as_posix()):
            return False, "Build-only mode: only the owner changes workspace.json or the hook configuration."
        if write and (_is_within(target, _package_dir())
                      or _TORQUE_INSTALL_RE.search(target.as_posix())):
            return False, ("Build-only mode: the installed Torque package enforces this mode; "
                           "only the owner changes it.")
        if write and (_in_interpreter(target, True) or _is_shadow_path(target, workspace)):
            return False, ("Build-only mode: this file could replace the gate at the hook's "
                           "Python startup (a torque package, torque.py, a .pth file, "
                           "sitecustomize/usercustomize, or the hook's Python installation); "
                           "only the owner changes it.")
        return True, ""
    if tool_name in SAFE_TOOLS:
        return True, ""
    if tool_name in MCP_LIKE_TOOLS:
        reason = _mcp_path_reason(tool_name, tool_input, clients, claude_dir, workspace, cwd)
        if reason:
            return False, f"Build-only mode: {reason}."
        return True, ""
    return False, (f"Build-only mode: {tool_name or 'this tool'} is not a tool this mode recognises, "
                   "so it is blocked. Use Bash, Read, Edit, Write, Grep or Glob, or ask the workspace "
                   "owner to run it.")


def _lsp_reason(tool_input: dict, clients: Path, claude_dir: Path, workspace: Path, cwd: Path) -> str:
    """LSP: only single-file operations, on a named file outside clients/, and
    every string argument checked like an MCP tool's."""
    operation = tool_input.get("operation")
    if not isinstance(operation, str) or operation not in LSP_FILE_OPERATIONS:
        shown = operation if isinstance(operation, str) and operation else "without an operation"
        return (f"LSP {shown} can return results from the language server's whole index, which covers "
                "clients/. Only documentSymbol, hover and goToDefinition on a file outside clients/ "
                "are allowed")
    path = tool_input.get("filePath")
    if not isinstance(path, str) or not path.strip() or "\n" in path or len(path) > 4096:
        return "LSP needs a filePath outside clients/ in this mode"
    return _mcp_path_reason("LSP", tool_input, clients, claude_dir, workspace, cwd, label="this LSP call",
                            hint="")


def _enter_worktree_reason(tool_input: dict, workspace: Path, cwd: Path) -> str:
    """EnterWorktree `path` must name a worktree under .claude/worktrees/ and not
    its clients/. Without `path` (a `name`, or nothing) Claude Code creates a new
    worktree there: a copy of the tracked tree plus the gitignored files
    .worktreeinclude names. That is blocked when the copy would hold client files."""
    dirs = [workspace.joinpath(*WORKTREES_DIR)]
    # A worktree that tracks workspace.json governs itself; the folder it sits in
    # (<workspace>/.claude/worktrees/) is where its siblings, and itself, live.
    parts = workspace.parts
    for i in range(len(parts) - 2):
        if parts[i].casefold() == WORKTREES_DIR[0] and parts[i + 1].casefold() == WORKTREES_DIR[1]:
            dirs.append(Path(*parts[:i + 2]))
    raw = tool_input.get("path")
    if raw is not None:
        if not isinstance(raw, str) or not raw.strip():
            return "EnterWorktree needs a path to a worktree under .claude/worktrees/"
        target = _resolve(cwd, _uri_path(raw))
        t = _cf(str(target))
        for folder in dirs:
            w = _cf(os.path.realpath(str(folder)))
            if t.startswith(w + os.sep):
                inner = re.split(r"[/\\]", t[len(w) + 1:])
                if len(inner) > 1 and inner[1] == "clients":
                    return "client context stays out of the AI session"
                return ""
        return ("EnterWorktree can only enter a worktree under .claude/worktrees/ in this mode; "
                "entering another folder could load its instructions and git state")
    return _worktree_copy_reason(workspace)


def _worktree_copy_reason(workspace: Path) -> str:
    """Why a new worktree of workspace would copy client files, or ""."""
    spec = ":(icase)clients"
    tracked = _git_output(workspace, ["ls-files", "--", spec])
    if tracked is None:
        return ("EnterWorktree could not confirm with git that a new worktree leaves clients/ out, so "
                "it is blocked")
    if tracked.strip():
        return ("clients/ has files tracked in git, so a new worktree would copy them. Ask the owner "
                "to untrack them (git rm -r --cached clients)")
    include = workspace / WORKTREE_INCLUDE
    if not os.path.lexists(include):
        return ""
    try:
        text = include.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = None
    listed = _git_output(workspace, ["ls-files", "--others", "--ignored", f"--exclude-from={include}",
                                     "--", spec]) if text is not None else None
    if text is None or listed is None or "clients" in text.casefold() or listed.strip():
        return (".worktreeinclude names files in clients/, so a new worktree would copy them. Ask the "
                "owner to change .worktreeinclude")
    return ""


def _uri_path(raw: str) -> str:
    """The path in a file: URI (file:///p, file://host/p, file:/p), parsed with
    urllib: host dropped, percent-escapes decoded, /C:/ read as C:/. Any other
    string is returned unchanged."""
    if raw[:5].casefold() != "file:":
        return raw
    try:
        path = urllib.parse.unquote(urllib.parse.urlparse(raw).path)
    except ValueError:
        return raw
    if re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    return path


def _command_strings(value: object, depth: int = 0):
    """Every string (or list of strings) held under a COMMAND_KEYS name, at any depth."""
    if depth > 8:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and key.casefold() in COMMAND_KEYS:
                if isinstance(item, str):
                    yield item
                elif isinstance(item, (list, tuple)) and all(isinstance(x, str) for x in item):
                    yield " ".join(shlex.quote(x) for x in item)
                    yield from item
            yield from _command_strings(item, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _command_strings(item, depth + 1)


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


def _mcp_path_reason(tool_name: str, tool_input: dict, clients: Path, claude_dir: Path, workspace: Path,
                     cwd: Path, label: str = "this MCP tool call",
                     hint: str = ". Disable file-reading MCP servers in a build-only workspace") -> str:
    """Treat every single-line string argument of an MCP tool as a possible path
    (a file:// URI included). Any that lands in clients/, the hook configuration
    or the installed Torque package is blocked; a tree-walking tool (search,
    tree, find, ...) rooted at or above clients/ is blocked too."""
    recursive = any(marker in tool_name.casefold() for marker in _MCP_RECURSIVE_MARKERS)
    for raw in _string_values(tool_input):
        if not raw or "\n" in raw or len(raw) > 4096:
            continue
        for candidate in dict.fromkeys([raw, _uri_path(raw)]):
            try:
                target = _resolve(cwd, candidate)
            except (OSError, ValueError):
                continue
            if _is_within(target, clients) or (recursive and _reaches(target, clients)):
                return f"{label} names client context{hint}"
            if _targets_guarded_file(target.as_posix()) or _is_within(target, claude_dir):
                return f"{label} names workspace.json or the hook configuration"
            if _is_within(target, _package_dir()) or _TORQUE_INSTALL_RE.search(target.as_posix()):
                return f"{label} names the installed Torque package that enforces this mode"
            if _in_interpreter(target, True) or _is_shadow_path(target, workspace):
                return f"{label} names a file that could replace the gate at the hook's Python startup"
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
# hook_command runs it with -I (isolated mode, Python 3.4+): the working
# directory, PYTHONPATH and the user site directory stay off sys.path, so a
# torque/ folder or sitecustomize.py planted in the workspace is never imported.
HOOK_SHIM_CODE = ("import os,sys;sys.excepthook=lambda t,e,b:(print('Build-only mode: the gate "
                  "could not load ('+t.__name__+': '+str(e)+'); blocking to fail closed.',"
                  "file=sys.stderr,flush=True),os._exit(2));from torque.gate import main;sys.exit(main())")


def hook_command(python: str) -> str:
    """The hook command for a given interpreter path (use forward slashes on Windows)."""
    return f'"{python}" -I -c "{HOOK_SHIM_CODE}"'


def _touched_paths(tool_input: dict, cwd: Path) -> list[Path]:
    """Paths a tool call names, resolved from cwd: every string argument as a
    path, and every word of a string that looks like a command."""
    out: list[Path] = []
    for raw in itertools.islice(_string_values(tool_input), 64):
        if not raw or "\x00" in raw or len(raw) > 20000:
            continue
        words = [_uri_path(raw)]
        if any(c in raw for c in " \t\n;&|<>"):
            try:
                expanded = _expand_braces(_decode_ansi_c(_expand_home_in_command(raw)))
            except ValueError:
                expanded = raw
            words += [_token_operand(t) for toks, _ in _segments_with_separators(expanded) for t in toks]
        for word in words[:512]:
            if not word or "\n" in word or len(word) > 4096:
                continue
            try:
                out.append(_resolve(cwd, word))
            except (OSError, ValueError):
                continue
    return list(dict.fromkeys(out))


def _gated_workspaces(cwd: Path, tool_input: dict) -> list[Path]:
    """Every build-only workspace that applies to a call: those at or above the
    event's cwd, those at or above the session's project directory
    (CLAUDE_PROJECT_DIR, which Claude Code sets for hooks, so leaving the
    workspace with `cd ..` does not end the session's gating), and those at or
    above any path the call names."""
    starts = [cwd]
    project = os.environ.get("CLAUDE_PROJECT_DIR")
    if project:
        starts.append(Path(os.path.realpath(project)))
    starts += _touched_paths(tool_input, cwd)
    gated: list[Path] = []
    checked: set[str] = set()
    for start in starts:
        key = _cf(str(start))
        if key in checked:
            continue
        checked.add(key)
        for folder, mode, _ in _workspace_chain(start):
            if mode == "build-only" and folder not in gated:
                gated.append(folder)
    return gated


def main() -> int:
    try:
        event = json.loads(sys.stdin.read())
        if not isinstance(event, dict):
            raise ValueError("hook input must be a JSON object")
        if "tool_name" not in event:
            raise ValueError("hook input has no tool_name")
        cwd = Path(os.path.realpath(str(event.get("cwd") or ".")))
        tool_input = event.get("tool_input")
        if tool_input is None:
            tool_input = {}
        if not isinstance(tool_input, dict):
            raise ValueError("tool_input must be an object")
        gated = _gated_workspaces(cwd, tool_input)
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
        print(f"Build-only mode: could not safely evaluate this call ({exc}); blocking to fail closed.",
              file=sys.stderr)
        return 2
    if not allowed:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
