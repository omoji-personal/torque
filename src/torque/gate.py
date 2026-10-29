"""Build-only mode: keep an AI session away from client orgs and client context.

This is a best-effort guard for an assistant following normal tool use. It scans
recognized tool calls (a Claude Code PreToolUse hook) for shapes that would reach a
Salesforce org, read or write client context, or disable the guard itself. It is not
a sandbox: a runtime-constructed command, an arbitrary script, a network tool, or a
host that does not wire up the hook can still get through. See docs/ai-access.md.
"""
from __future__ import annotations
import base64
import errno
import glob
import itertools
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import urllib.parse
from functools import lru_cache
from pathlib import Path

# sf/sfdx leading command words that stay local and never touch an org, as long as
# no org flag (_sf_org_flags) also appears on the line. This is the one classifier
# both modes use: connected_routes.SF_LOCAL is this same set. `cmdt generate fromorg`
# is the exception (it exports an org's records), so it is NOT local. A command with
# a `-h`/`--help`/`--version` flag describes itself and is local too (_sf_is_local).
SF_LOCAL_COMMANDS = {("project", "generate"), ("project", "convert"), ("project", "list", "ignored"),
                     ("template", "generate"), ("schema", "generate"), ("cmdt", "generate"),
                     ("lightning", "generate"), ("apex", "generate"), ("agent", "generate"),
                     # the template generators' own aliases (sf static-resource generate is
                     # template generate static-resource; generate metadata is schema generate)
                     ("static-resource", "generate"), ("visualforce", "generate"), ("analytics", "generate"),
                     ("generate", "metadata"),
                     ("version",), ("help",), ("commands",), ("whatsnew",), ("which",), ("search",), ("info",),
                     ("doctor",), ("autocomplete",), ("config", "list"), ("config", "get"),
                     ("plugins",), ("plugins", "inspect")}
SF_CMDT_FROMORG = ("cmdt", "generate", "fromorg")
# code-analyzer runs local static analysis (no org); build-only allows it with its
# roots scoped away from clients/ (SF_TREE_READERS). It is not in the shared local
# set because connected mode asks the consultant about it (it can run rules plugins).
SF_CODE_ANALYZER = {("code-analyzer", "run"), ("code-analyzer", "rules")}
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
# Argument names under which an MCP call names the server it reads from (a resource
# read: ReadMcpResourceTool's `server`, Antigravity's `ServerName`).
_MCP_SERVER_KEYS = ("server", "servername", "server_name")
# Salesforce org and login hosts, by suffix. An MCP server whose name does not say
# Salesforce is still org access when a string argument names one of these hosts.
# SF_PUBLIC_HOSTS are Salesforce's own public sites under those suffixes; they hold
# no org. Connected mode's network check keeps its own, looser pattern (SF_HOSTS).
SF_HOST_SUFFIXES = ("salesforce.com", "force.com", "my.site.com", "salesforce-setup.com", "salesforce-sites.com",
                    "cloudforce.com", "database.com", "visualforce.com", "documentforce.com", "lightning.com",
                    "sfdc.net")
SF_PUBLIC_HOSTS = frozenset({"salesforce.com", "www.salesforce.com", "help.salesforce.com",
                             "developer.salesforce.com", "trailhead.salesforce.com", "appexchange.salesforce.com",
                             "status.salesforce.com", "architect.salesforce.com", "admin.salesforce.com",
                             "resources.docs.salesforce.com", "releasenotes.docs.salesforce.com"})
# A whole host name ending in one of the suffixes: workforce.com, website.com and
# example.salesforce.com.example.org do not match.
_SF_HOST_RE = re.compile(r"(?<![A-Za-z0-9.-])((?:[A-Za-z0-9-]+\.)*(?:"
                         + "|".join(re.escape(suffix) for suffix in SF_HOST_SUFFIXES) + r"))(?!\.?[A-Za-z0-9-])",
                         re.IGNORECASE)
# MCP servers that drive a browser or the desktop, by server name. The gate cannot see
# which page or window such a tool acts on, so build-only mode blocks them and
# connected mode refuses them (connected_routes classifies with these same patterns).
BROWSER_SERVER = re.compile(r"(chrome|playwright|puppeteer|browser|firefox|safari|webdriver|selenium)",
                            re.IGNORECASE)
DESKTOP_SERVER = re.compile(r"(computer[-_]?use|desktop|applescript|automation)", re.IGNORECASE)
BROWSER_TOOL_REASON = ("Build-only mode: browser and desktop control tools are blocked, because the gate cannot "
                       "see which page or window such a tool acts on, so it cannot keep an org or client page "
                       "out of the session. Do that step yourself outside the AI session; for a public page, "
                       "use web fetch or web search.")
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
# Hook configuration: Claude Code's settings files and Antigravity's hooks.json.
SETTINGS_RE = re.compile(r"(^|[/\\])(\.claude[/\\]settings[^/\\]*\.json|\.agents[/\\]hooks\.json)$", re.IGNORECASE)
# Copy and archive tools that read a whole directory tree when given a recursive flag
# (tar always does).
RECURSIVE_COPY_HEADS = {"cp", "scp", "rsync", "zip"}
# Short options that take a value, per copy tool: in a cluster (-rA2, -9r, -rtDIR)
# the first of them takes the rest, so letters after it are not options. Digits
# (zip's compression level) are allowed anywhere in a cluster.
_COPY_VALUE_LETTERS = {"cp": "tS", "scp": "cFiJloPSD", "rsync": "efBMT", "zip": "bntOxi"}
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
# Brace expansion: prefix{a,b}suffix within one shell word. The group is matched
# on its own, and the prefix and suffix are found by a scan (see _expand_braces), so
# expansion takes linear time on any input. The group's first part stops at the
# first comma, which keeps the match itself from backtracking over later commas.
_BRACE_GROUP_RE = re.compile(r"\{([^{}\s,]*,[^{}\s]*)\}")
# Characters that end a brace word's prefix or suffix.
_BRACE_WORD_STOP = frozenset(" \t\n\r\f\v{}'\"")
# The longest command, or other string the gate parses, that it reads. A longer one
# is blocked before any pattern runs: one regular-expression call holds the GIL, so
# the watchdog cannot interrupt it, and some shapes take time that grows with the
# square of the length.
MAX_INPUT_CHARS = 20_000
# Brace expansion can multiply a command's length ({a,b,c,...} after a long prefix);
# past this many characters the expanded command is blocked too.
MAX_EXPANDED_CHARS = 4 * MAX_INPUT_CHARS
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
# Reserved words that can start a segment holding a cd: if cd ..; then cd ..; do cd ..
_SHELL_KEYWORDS = {"if", "then", "else", "elif", "do", "while", "until", "!"}
# A parameter expansion the gate cannot know: $NAME, ${...}, $1, $@, and a bare $
# left where the splitter cut $(...) apart.
_PARAM_RE = re.compile(r"\$(?:\{[^}]*\}|[A-Za-z_][A-Za-z0-9_]*|[0-9@*#?!$-]|$)")
_BRACED_PARAM_RE = re.compile(r"\$\{[^{}]*\}")
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
# Each hook call gets one wall-clock budget, in seconds, for everything it reads from
# the disk (path resolution, glob expansion, git queries), and one budget of glob
# matches. Past either the call is blocked, so the hook answers long before Claude
# Code's own hook timeout, which would let the call run.
GATE_TIME_BUDGET = 5.0
GLOB_MATCH_LIMIT = 10_000
# The watchdog in main() ends the hook process this long after the budget if the
# cooperative checks have not already blocked the call.
WATCHDOG_GRACE = 0.5
_deadline: float | None = None
_glob_matches = 0
# Each distinct glob pattern is expanded, and charged, once per call.
_glob_cache: dict[str, tuple[list[Path], list[Path]]] = {}
# clients_index_count runs two git subprocesses; its answer for a workspace does not
# change within one call, so it is memoized per call (keyed by the resolved workspace).
# This keeps a connected session with many client folders, whose guarded-folder passes
# all ask about the same workspace, from spawning a git pair per folder.
_index_cache: dict[str, int | None] = {}


class BudgetExceeded(Exception):
    """The call used up the gate's time or glob budget."""


def _spend(matches: int = 0) -> None:
    """Charge the current call's budgets; raise BudgetExceeded past either."""
    global _glob_matches
    if _deadline is not None and time.monotonic() > _deadline:
        raise BudgetExceeded(f"the gate's {GATE_TIME_BUDGET:g}-second time budget for one call ran out")
    if matches:
        _glob_matches += matches
        if _glob_matches > GLOB_MATCH_LIMIT:
            raise BudgetExceeded(f"globs in this call match more than {GLOB_MATCH_LIMIT} paths")


def _remaining() -> float | None:
    """Seconds left in the current call's time budget, or None outside a call."""
    return None if _deadline is None else max(0.0, _deadline - time.monotonic())


def _budget_reason(exc: BudgetExceeded) -> str:
    # Neutral prefix: a spent budget can be hit during workspace discovery, before
    # the governing mode (build-only or connected) is known, as well as inside a
    # build-only or connected check. The cause (time or globs) is named by `exc`.
    return (f"Torque gate: {exc}, so the call could not be checked in time; blocking to fail closed. "
            "Name fewer paths or narrower globs, such as project/src/*.py.")


NARROW_PATH_HINT = " Pass a narrower path, such as project/ or src/, instead of the workspace root."
# Markers that a block already names an in-session next step (narrow the scope, name
# paths under project/, preview with git clean -n, untrack with git rm --cached): a
# scope block the consultant's rule lets the assistant retry, not a handoff.
_SCOPE_BLOCK_MARKERS = ("project/", "git clean -n", "--cached")


def _command_block_message(reason: str) -> str:
    """The build-only message for a blocked Bash or PowerShell command. A scope block
    (one that already tells how to narrow the call) is left as its own guidance, so it
    does not also say to stop and hand off; a boundary block (org access, client
    context, configuration or the gate) keeps the handoff. The period is never doubled."""
    core = f"Build-only mode: {reason}"
    if not core.rstrip().endswith((".", "!", "?")):
        core += "."
    if not any(marker in reason for marker in _SCOPE_BLOCK_MARKERS):
        core += " Run it yourself outside the AI session."
    return core


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
    _spend()
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


# Connected mode's consent and approval records, and the tier 1 approval key.
APPROVAL_FILE_RE = re.compile(r"(^|[/\\])clients[/\\][^/\\]+[/\\](consent\.json|consent-evidence([/\\]|$)|"
                              r"approvals([/\\]|$))", re.IGNORECASE)
APPROVAL_KEY_RE = re.compile(r"(^|[/\\])torque[/\\]approval\.key$", re.IGNORECASE)


def _targets_guarded_file(text: str) -> bool:
    """True if text (a raw Bash token, or a resolved path) names workspace.json,
    a .claude/settings*.json hook config or .worktreeinclude, a client's consent
    or approval records, or the approval key, case-insensitively. Only the owner
    changes these; an AI session must not be able to switch the mode off, grant
    its own approvals, or list clients/ for copying into a new worktree."""
    folded = text.casefold()
    if "workspace.json" in folded or WORKTREE_INCLUDE in folded:
        return True
    if APPROVAL_FILE_RE.search(text) or APPROVAL_KEY_RE.search(text):
        return True
    return bool(SETTINGS_RE.search(text))


def _basename(tok: str) -> str:
    name = re.split(r"[/\\]", tok)[-1].lower()
    return re.sub(r"\.(cmd|exe|bat|ps1)$", "", name)


# The Salesforce CLI's own entry script, as `node .../@salesforce/cli/bin/run.js` starts it.
_SF_ENTRY_RE = re.compile(r"(^|[/\\])(@salesforce[/\\]cli|sfdx-cli)[/\\]bin[/\\](run|dev)(\.js|\.cmd)?$",
                          re.IGNORECASE)


def _is_sf_token(tok: str) -> bool:
    folded = tok.casefold()
    if folded == "@salesforce/cli" or folded.startswith("@salesforce/cli@") or _SF_ENTRY_RE.search(tok):
        return True
    return _basename(tok) in ("sf", "sfdx")


# Commands whose arguments are data they look up, search or print, never a program
# they run. A `sf`/`sfdx`/`@salesforce/cli` word that is one of these commands'
# arguments (which sf, where.exe sf, Get-Command sf, rg sfdx, echo sf) is not an sf
# call. Every other position keeps reading such a word as a call, as before, so an sf
# call behind a runner (cmd /c, Start-Process, node .../run.js, env, time, sudo, xargs,
# npx, then/do/else) is still caught: the gate fails closed on sf, and exempts only
# these known readers.
_SF_DATA_HEADS = {"which", "where", "whereis", "type", "get-command", "gcm", "rg", "grep", "egrep", "fgrep",
                  "ack", "ag", "select-string", "sls", "findstr", "echo", "printf", "cat", "head", "tail",
                  "less", "more", "test", "[", "[[", "write-output", "write-host"}
# Options of a search command that run a program for each match (or as a
# preprocessor), so a word after the command is not merely data: find -exec, fd -x,
# rg --pre. With one of these the sf word is read as a call, as everywhere else.
_EXEC_OPTIONS = {"find": ("-exec", "-execdir", "-ok", "-okdir"),
                 "fd": ("-x", "--exec", "-X", "--exec-batch"),
                 "rg": ("--pre",)}
# npm/pnpm/yarn subcommands that only list or describe packages (npm ls @salesforce/cli).
_PKG_LIST_VERBS = {"ls", "list", "la", "ll", "view", "info", "show", "outdated", "why", "explain", "search",
                   "find", "s", "se", "v"}


def _sf_command_position(words: list[str], index: int) -> bool:
    """True when the sf word at words[index] is read as an sf call. That is every
    position except an argument of a command that only looks up, searches or prints its
    arguments (_SF_DATA_HEADS, `command -v`/`-V`, `npm ls` and the like, a `find` with
    no -exec). The head of the simple command is its first word that is not an
    assignment or a shell keyword (then, do, else, ...), so `then sf ...` reads sf as
    the head. Defaulting to a call keeps every runner that wraps sf caught."""
    head = 0
    while head < len(words) and (_ASSIGN_RE.match(words[head]) or words[head] in _SHELL_KEYWORDS):
        head += 1
    if head >= index:
        return True  # sf is the head of its simple command
    name = _basename(words[head])
    between = words[head + 1:index]
    runs = _EXEC_OPTIONS.get(name, ())
    if runs and any(w.split("=", 1)[0] in runs for w in words):
        return True  # find -exec sf, fd -x sf, rg --pre sf run the program
    if name in _SF_DATA_HEADS or name in ("find", "fd"):
        return False  # which sf, rg sfdx, echo sf, find . -name sf: data, not a call
    if name in ("command", "builtin") and any(b in ("-v", "-V") for b in between):
        return False  # command -v sf looks the program up, like which
    if name in ("npm", "pnpm", "yarn") and words[head + 1:head + 2] and words[head + 1].casefold() in _PKG_LIST_VERBS:
        return False  # npm ls @salesforce/cli lists packages
    return True


def _sf_topic(rest: list[str]) -> tuple[str, ...]:
    """The leading command words of an sf line (the words before the first flag)."""
    words: list[str] = []
    for tok in rest:
        if tok.startswith("-"):
            break
        words.append(tok)
    return tuple(words)


def _sf_org_flags(topic: tuple[str, ...]) -> set[str]:
    """The flags that name an org for this sf command. code-analyzer has no org;
    its `-o` is `--output-file` and its `-v` is `--view`, so neither counts (but a
    long org flag such as `--target-org` still does). `-v` names the Dev Hub only
    for package and org commands; elsewhere it carries a value (mirrors
    connected_routes._sf_org_flags, the shared reading)."""
    if topic[:1] == ("code-analyzer",):
        return ORG_FLAGS - {"-o", "-v"}
    if topic[:1] in (("package",), ("package1",), ("org",)):
        return set(ORG_FLAGS)
    return ORG_FLAGS - {"-v"}


def _sf_has_org_flag(rest: list[str], topic: tuple[str, ...]) -> bool:
    flags = _sf_org_flags(topic)
    return any(tok.split("=", 1)[0] in flags for tok in rest)


def _sf_is_help(rest: list[str]) -> bool:
    """oclif shows help, and runs nothing, when -h or --help appears among the
    arguments before a `--` (its help check reads every argument)."""
    for tok in rest:
        if tok == "--":
            return False
        if tok in ("-h", "--help"):
            return True
    return False


def _sf_is_version(rest: list[str]) -> bool:
    """oclif prints its version, and runs nothing, only when the first argument is
    --version or -v. A command's own --version (sf package1 version create --version
    1.0) is that command's operand."""
    return rest[:1] in (["--version"], ["-v"])


def _sf_doctor_command(rest: list[str]) -> str | None:
    """The command `sf doctor` is told to run, as the installed doctor reads it, or
    None. oclif takes the value of a flag as the next word (`-c X`, `--command X`) or
    attached (`-cX`, `-c=X`, `--command=X`), a short flag may share a word with doctor's
    other short flags (`-lc X`), and `--` ends the options. The doctor puts `sf` in front
    of a command that lacks it, so that is the command returned (several -c flags are
    joined, so each is judged). `sf doctor --command X` runs X, so it reaches whatever
    X reaches; both modes read it as X (build-only blocks it as non-local, connected
    judges it as X). The caller reads help first (_sf_is_help): `--help` runs nothing."""
    if _sf_topic(rest)[:1] != ("doctor",):
        return None
    found: list[str] = []
    i = 0
    while i < len(rest):
        tok = rest[i]
        i += 1
        if tok == "--":
            break
        value = None
        if tok.startswith("--command"):
            name, eq, attached = tok.partition("=")
            if name == "--command":
                value = attached if eq else (rest[i] if i < len(rest) else "")
                i += 0 if eq else 1
        elif tok.startswith("-") and not tok.startswith("--"):
            for at, letter in enumerate(tok[1:], 2):
                if letter == "c":
                    value = tok[at:]
                    if value.startswith("="):
                        value = value[1:]
                    elif not value:
                        value = rest[i] if i < len(rest) else ""
                        i += 1
                    break
                if letter in "pd":         # doctor's other value flags take the rest
                    if at == len(tok):
                        i += 1
                    break
        if value is not None:
            words = value.split(None, 1)
            found.append(value if words and _is_sf_token(words[0]) else ("sf " + value if words else ""))
    return "; ".join(found) if found else None


# sf commands that reach the configured default org with no org flag on the line, so
# they are not local in either mode. `agent generate` (agent-spec defaults to the
# configured org); `doctor --command` runs the command it is given (_sf_doctor_command).
def _sf_reaches_org_without_flag(rest: list[str]) -> bool:
    return _sf_topic(rest)[:2] == ("agent", "generate") or _sf_doctor_command(rest) is not None


def _sf_is_local(rest: list[str]) -> bool:
    """True when an sf/sfdx command stays local (no org), by the shared classifier: a
    leading command in SF_LOCAL_COMMANDS or SF_CODE_ANALYZER, but never `cmdt generate
    fromorg`, a `plugins` subcommand other than `inspect`, or a command that reaches an
    org with no org flag (_sf_reaches_org_without_flag). Help and the global version
    flag are read before this (_sf_is_help, _sf_is_version)."""
    topic = _sf_topic(rest)
    if topic[:3] == SF_CMDT_FROMORG:
        return False
    if topic[:1] == ("plugins",) and topic[1:2] not in ((), ("inspect",)):
        # plugins install/link/update/uninstall/reset change what is installed.
        return False
    if _sf_reaches_org_without_flag(rest):
        return False
    return any(topic[:len(key)] == key for key in SF_LOCAL_COMMANDS | SF_CODE_ANALYZER)


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
        elif not tok.startswith("--") and re.search(r"[fF]", tok[1:]):
            # A file value attached to -f or -F in a short-option group (git commit
            # -Fmsg, -qFmsg, sed/awk/grep -fscript): the rest of the group.
            value = tok[1 + re.search(r"[fF]", tok[1:]).end():]
            if not value:
                return ""
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
    if pattern in _glob_cache:
        cached_paths, cached_roots = _glob_cache[pattern]
        return list(cached_paths), list(cached_roots)
    result = _expand_glob(pattern, prefix)
    if _deadline is not None:
        _glob_cache[pattern] = result
    return list(result[0]), list(result[1])


def _expand_glob(pattern: str, prefix: Path) -> tuple[list[Path], list[Path]]:
    """Expand one absolute glob pattern, charging each match to the call's glob
    budget. Over _GLOB_LIMIT matches it is read as a recursive root (prefix)."""
    found: list[str] = []
    try:
        for match in glob.iglob(pattern):
            _spend(1)
            found.append(match)
            if len(found) > _GLOB_LIMIT:
                return [], [prefix]
    except (OSError, ValueError, re.error):
        return [], [prefix]
    out: list[Path] = []
    for match in found:
        _spend()
        out.append(Path(os.path.realpath(match)))
    return out, []


def _dollar_variants(operand: str, cwd: Path, workspace: Path | None) -> list[str]:
    """operand with every parameter expansion the gate cannot know ($R, ${R%x},
    $1) replaced by each directory it could stand for: the current directory,
    the folders between it and the workspace root, and the root itself. So a
    path or search root built from a variable counts as reaching the workspace
    root, as a cd to one already does. Without a workspace, operand as it is."""
    if workspace is None:
        return [operand]
    expanded = _PWD_TOKEN_RE.sub(lambda _m: str(cwd), _expand_home(operand))
    if not _PARAM_RE.search(expanded):
        return [operand]
    anchors = [cwd]
    if _is_within(cwd, workspace):
        anchors += [p for p in cwd.parents if _is_within(p, workspace)]
    anchors.append(workspace)
    return [_PARAM_RE.sub(lambda _m, a=a: a.as_posix(), expanded) for a in dict.fromkeys(anchors)]


def _token_paths(tok: str, cwd: Path, workspace: Path | None = None) -> tuple[list[Path], list[Path]]:
    """Every path a raw Bash token can name from cwd, as (paths, recursive roots).
    With a workspace, an unknown parameter expansion is read as each directory
    from cwd up to the workspace root (_dollar_variants)."""
    operand = _token_operand(tok)
    if not operand or operand.startswith("-"):
        return [], []
    paths: list[Path] = []
    roots: list[Path] = []
    for variant in _dollar_variants(operand, cwd, workspace):
        try:
            paths.append(_resolve(cwd, variant))
        except (OSError, ValueError):
            pass
        matches, found = _glob_paths(variant, cwd)
        paths += matches
        roots += found
    return paths, roots


def _token_is_client_path(tok: str, clients: Path, cwd: Path, workspace: Path) -> bool:
    if not tok:
        return False
    # `workspace` is the real workspace root (not clients.parent, which is only the
    # workspace when the guarded folder is clients/ itself; in connected mode clients
    # is a sibling client or initiative folder, whose parent is clients/).
    paths, roots = _token_paths(tok, cwd, workspace)
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


def _token_names_agents_dir(tok: str, workspace: Path, cwd: Path) -> bool:
    """True when a token names the workspace's .agents folder itself: removing,
    renaming or replacing it takes Antigravity's hooks.json with it. The rules,
    skills and worker roles inside it stay editable."""
    if not tok:
        return False
    paths, _ = _token_paths(tok, cwd)
    agents = _cf(os.path.realpath(str(workspace / ".agents")))
    return any(_cf(str(path)) == agents for path in paths)


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
        return (any(t == "--recursive" or _grep_long_recursive(t) or _short_flag_has(t, "rR", _GREP_VALUE_LETTERS + "d")
                    for t in rest) or _grep_directories_recurse(rest))
    if head == "ls":
        return any(t == "--recursive" or _short_flag_has(t, "R", "wTI") for t in rest)
    if head in TAR_HEADS:
        return True
    if head in RECURSIVE_COPY_HEADS:
        return any(t in ("--recursive", "--archive") or _short_flag_has(t, "rRa", _COPY_VALUE_LETTERS[head])
                   for t in rest)
    if head == "diff":
        return any(t == "--recursive" or _short_flag_has(t, "r", "xXSFLCUDWI") for t in rest)
    return False


def _grep_long_recursive(tok: str) -> bool:
    """grep's --recursive and --dereference-recursive, and the abbreviations
    getopt accepts for them (--recur, --deref)."""
    name = tok.split("=", 1)[0]
    return name.startswith("--") and (_is_long_flag(name, "--recursive", 5)
                                       or _is_long_flag(name, "--dereference-recursive", 5))


# grep's short options that take a value, other than -d.
_GREP_VALUE_LETTERS = "ABCmDef"


def _grep_directories_recurse(rest: list[str]) -> bool:
    """grep -d recurse, -drecurse, --directories=recurse (and abbreviations of the
    option and of `recurse`), which search a tree like -r."""
    for j, tok in enumerate(rest):
        if tok == "--":
            break
        value = None
        if tok.startswith("--"):
            name, eq, val = tok.partition("=")
            if _is_long_flag(name, "--directories", 4):
                value = val if eq else (rest[j + 1] if j + 1 < len(rest) else "")
        elif tok.startswith("-") and len(tok) > 1:
            for index, letter in enumerate(tok[1:], start=1):
                if letter in _GREP_VALUE_LETTERS:
                    break
                if letter == "d":
                    value = tok[index + 1:] or (rest[j + 1] if j + 1 < len(rest) else "")
                    break
        if value is not None and len(value) >= 3 and "recurse".startswith(value):
            return True
    return False


# find's options that come before its paths: the traversal options -H, -L and -P
# (GNU and BSD), BSD's -E, -X, -s and -x, GNU's -D DEBUGOPTS and -OLEVEL. BSD's -f PATH
# names a path itself.
_FIND_LEADING_SWITCHES = frozenset({"-H", "-L", "-P", "-E", "-X", "-s", "-x"})


def _find_roots(rest: list[str]) -> list[str]:
    """find's path operands. Its leading options (_FIND_LEADING_SWITCHES, -D VALUE,
    -O LEVEL, BSD's -f PATH, and the `--` that ends them) are read first; then, by the POSIX grammar, the paths are
    the words before the expression, which starts at the first operand that begins
    with `-`, or is `(` or `!`. With no path, find searches the current directory, so
    the caller's default (.) applies (an empty list here)."""
    roots: list[str] = []
    j = 0
    while j < len(rest):
        tok = rest[j]
        if tok in _FIND_LEADING_SWITCHES or re.fullmatch(r"-O\d*", tok):
            j += 1
        elif tok in ("-D", "-f") and j + 1 < len(rest):
            if tok == "-f":
                roots.append(rest[j + 1])
            j += 2
        else:
            if tok == "--":
                j += 1               # the option terminator: the paths follow it
            break
    for tok in rest[j:]:
        if tok.startswith("-") or tok in ("(", "!"):
            break
        roots.append(tok)
    return roots


def _recursive_search_targets(head: str, rest: list[str]) -> list[str]:
    """The path arguments a recursive search tool will actually read, as best we
    can tell from its usual grammar. grep/rg/ag/ack/fd take PATTERN [PATH...], so
    their first non-flag argument is the search pattern, not a path; tree and ls
    take PATH[...] directly (their value options, -L/-I/-w and the like, do not name
    a path); find takes PATH[...] before its expression (_find_roots). With no path,
    the caller defaults to the current directory, so these never read as rooted at a
    stray option value."""
    if head == "find":
        return _find_roots(rest)
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
    # tree and ls take PATH[...] directly; these options take a value, so it is not
    # read as a path (tree -L 2, tree -I node_modules, ls -I '*.tmp' -w 80).
    "tree": {"-L", "-P", "-I", "-o", "-H", "-T", "--charset", "--filelimit", "--sort", "--timefmt",
             "--fromfile", "--matchdirs"},
    "ls": {"-w", "-T", "-I", "--block-size", "--format", "--time-style", "--tab-size", "--sort",
           "--quoting-style", "--indicator-style"},
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


def _tar_operands(args: list[str], cwd: Path, dirs: list[Path] | None = None) -> list[tuple[str, Path]] | None:
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
                if dirs is not None:
                    dirs.append(base)
            else:
                out.append((value, base))
        j += 1
    return out or [(".", base)]


def _recursive_search_reaches(head: str, rest: list[str], clients: Path, cwd: Path, workspace: Path) -> bool:
    """A recursive search tool's target defaults to cwd when it names no path
    argument at all (e.g. `rg foo`, bare `tree`, `find -name x`)."""
    roots = _recursive_search_targets(head, rest) or ["."]
    return any(_root_reaches(raw, clients, cwd, workspace) for raw in roots)


def _root_reaches(raw: str, clients: Path, cwd: Path, workspace: Path) -> bool:
    """True when a recursive operation rooted at raw (a raw token, glob allowed)
    could reach clients/. A root built from a variable counts as reaching the
    workspace root, so `workspace` must be the real root (see _token_is_client_path)."""
    paths, roots = _token_paths(raw, cwd, workspace)
    return any(_reaches(p, clients) for p in paths + roots)


LN_REASON = ("a link to client context, the workspace root or a folder above it would let a later command "
             "reach clients/. Link to a path inside project/")


_LINK_ITEM_TYPES = {"symboliclink", "junction", "hardlink"}


def _link_target_reaches(target: str, link: str, clients: Path, cwd: Path, workspace: Path) -> bool:
    """A link target, read both from the current directory and from the folder
    the link is made in (Windows reads a relative symbolic target from there),
    reaching clients/ or above it."""
    if not target or _PARAM_RE.search(target) or "`" in target:
        return True
    try:
        link_dir = _resolve(cwd, os.path.dirname(link) or ".") if link else cwd
    except (OSError, ValueError):
        return True
    return any(_root_reaches(target, clients, base, workspace) for base in dict.fromkeys([cwd, link_dir]))


def _mklink_reaches(rest: list[str], clients: Path, cwd: Path, workspace: Path) -> bool:
    """cmd's `mklink [/D|/H|/J] LINK TARGET`."""
    operands = [t for t in rest if not re.match(r"^/{1,2}[A-Za-z]$", t)]
    if len(operands) < 2:
        return False
    return _link_target_reaches(operands[1], operands[0], clients, cwd, workspace)


def _new_item_reaches(rest: list[str], clients: Path, cwd: Path, workspace: Path) -> bool:
    """PowerShell's `New-Item -ItemType SymbolicLink|Junction|HardLink -Path P
    -Target T` (or -Value, -Name, -Type)."""
    values: dict[str, str] = {}
    for j, tok in enumerate(rest):
        if tok.startswith("-") and j + 1 < len(rest):
            name = tok[1:].split(":", 1)[0].casefold()
            for full in ("itemtype", "type", "path", "name", "target", "value"):
                if full.startswith(name) and len(name) >= 2:
                    values.setdefault("itemtype" if full == "type" else full, rest[j + 1])
                    break
    if values.get("itemtype", "").casefold() not in _LINK_ITEM_TYPES:
        return False
    target = values.get("target", values.get("value", ""))
    return _link_target_reaches(target, values.get("path", values.get("name", "")), clients, cwd, workspace)


def _ln_reaches(rest: list[str], clients: Path, cwd: Path, workspace: Path) -> bool:
    """True when `ln` (symbolic or hard) would make a link whose target is
    clients/, inside it, the workspace root or any folder above it. A relative
    symbolic target is read from the folder the link is made in; a hard link's,
    or one made with -r/--relative, from the current directory."""
    symbolic = relative = False
    target_dir: str | None = None
    operands: list[str] = []
    ended = False
    j = 0
    while j < len(rest):
        tok = rest[j]
        if ended or not tok.startswith("-") or tok == "-":
            operands.append(tok)
        elif tok == "--":
            ended = True
        elif tok.startswith("--"):
            name, eq, value = tok.partition("=")
            if _is_long_flag(name, "--symbolic", 4):
                symbolic = True
            elif _is_long_flag(name, "--relative", 3):
                relative = True
            elif _is_long_flag(name, "--target-directory", 3):
                target_dir = value if eq else (rest[j + 1] if j + 1 < len(rest) else "")
                j += 0 if eq else 1
            elif _is_long_flag(name, "--suffix", 4) and not eq:
                j += 1
        else:
            group = tok[1:]
            for index, letter in enumerate(group):
                if letter == "s":
                    symbolic = True
                elif letter == "r":
                    relative = True
                elif letter in "tS":
                    value = group[index + 1:]
                    if not value:
                        value = rest[j + 1] if j + 1 < len(rest) else ""
                        j += 1
                    if letter == "t":
                        target_dir = value
                    break
        j += 1
    if target_dir is not None:
        targets, link_dir = operands, target_dir
    elif len(operands) >= 2:
        targets, last = operands[:-1], operands[-1]
        try:
            link_dir = last if os.path.isdir(_resolve(cwd, last)) else (os.path.dirname(last) or ".")
        except (OSError, ValueError):
            return True
    else:
        targets, link_dir = operands, "."
    if not targets:
        return False
    if symbolic and not relative:
        if _PARAM_RE.search(link_dir) or "`" in link_dir:
            return True
        try:
            base = _resolve(cwd, link_dir or ".")
        except (OSError, ValueError):
            return True
    else:
        base = cwd
    return any(_root_reaches(raw, clients, base, workspace) for raw in targets)


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


def _sf_tree_reader_reaches(rest: list[str], clients: Path, cwd: Path, workspace: Path) -> bool:
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
    return any(_root_reaches(raw, clients, cwd, workspace) for raw in roots)


def _is_long_flag(tok: str, flag: str, shortest: int) -> bool:
    """True when tok is flag or an abbreviation git would accept for it (any
    prefix at least `shortest` characters long; git rejects an ambiguous one)."""
    name = tok.split("=", 1)[0]
    return len(name) >= shortest and flag.startswith(name)


def _reads_untracked(tok: str) -> bool:
    return _is_long_flag(tok, "--untracked", 3) or _is_long_flag(tok, "--no-index", 6)


def _git_reaches(rest: list[str], clients: Path, cwd: Path, workspace: Path) -> bool:
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
        return any(_root_reaches(raw, clients, base, workspace) for raw in paths)
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
    return any(_root_reaches(raw, clients, base, workspace) for raw in (paths or ["."]))


_GIT_GREP_PATTERN_FLAGS = {"-e", "-f", "--regexp", "--file"}
_GIT_GREP_VALUE_FLAGS = {"-A", "-B", "-C", "--after-context", "--before-context", "--context",
                         "-m", "--max-count", "--max-depth", "--threads", "-O", "--open-files-in-pager"}
_STASH_ACTIONS = {"push", "save", "show", "list", "apply", "pop", "drop", "branch", "clear", "create", "store"}
# A stash's untracked files are its third parent: stash^3, stash@{0}^3, refs/stash^3.
_STASH_UNTRACKED_REF_RE = re.compile(r"(?<![A-Za-z0-9_.-])(?:refs/)?stash(?:@\{[^}]*\}|~\d*)*\^3")
GIT_STAGE_REASON = ("git add, git stage, git update-index --add or git hash-object -w at or above client "
                    "context, the .claude hook configuration or the hook's environment would copy them into "
                    "git. Name paths such as project/")
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


_CLIENT_SPEC = ":(icase)clients"
GIT_TRACKED_REASON = ("client files are in git's index in this workspace, so git commands other than git "
                      "status and git rm --cached of clients/ are blocked. Client files must stay untracked: "
                      "run git rm -r --cached clients")
# When git itself could not answer (missing, timed out, or an error), the fix is not
# to untrack but to make git work; naming that keeps the agent from running the wrong
# remedy (claude-A2a#14).
GIT_UNKNOWN_REASON = ("git could not confirm whether client files are tracked in this workspace (git is "
                      "missing, timed out, or errored), so git commands are blocked to fail closed. Check that "
                      "git is on PATH and the repository is readable, outside the AI session")


def _git_add_roots(args: list[str]) -> list[str] | None:
    """The pathspecs `git add`/`git stage` covers. A pathspec file, or no pathspec
    with -A, --all or -u (the whole tree), give None: the repository top."""
    options = args[:args.index("--")] if "--" in args else args
    if any(_is_long_flag(t, "--pathspec-from-file", 12) for t in options):
        return None
    specs = _git_pathspecs(args, {"--chmod"})
    whole = any((_is_long_flag(t, "--all", 3) or _is_long_flag(t, "--no-ignore-removal", 6)
                 or _is_long_flag(t, "--update", 3)) if t.startswith("--") else _short_flag_has(t, "Au", "")
                for t in options)
    if not specs and whole:
        return None
    return specs


def _clients_in_index(workspace: Path) -> bool:
    """True when git's index for the workspace holds a file under clients/, or when
    git cannot say. Outside a repository there is none."""
    count = clients_index_count(workspace)
    return count is None or count > 0


def _git_stage_reason(rest: list[str], clients: Path, claude_dir: Path, cwd: Path, workspace: Path) -> str:
    """Client files enter git through `git add`/`git stage`, `git update-index
    --add` and `git hash-object -w`. Each is blocked when its paths reach
    clients/, .claude/ or the hook's environment. And while client files are in
    the index (or git cannot say), every git command is blocked except git status
    without -v/--verbose and git rm --cached of paths under clients/."""
    try:
        base, trees, sub, args = _git_parse(rest, cwd)
    except (OSError, ValueError):
        return GIT_STAGE_REASON
    protected = _git_protected(clients, claude_dir)
    if sub in ("add", "stage"):
        specs = _git_add_roots(args)
        if specs is None:
            reaches = bool(trees) or _toplevel_reaches(base, protected)
        else:
            reaches = bool(specs) and (bool(trees) or _pathspecs_reach(specs, base, protected))
        if reaches:
            return GIT_STAGE_REASON
    elif sub in ("update-index", "hash-object"):
        writes = (any(_is_long_flag(t, "--add", 4) or _is_long_flag(t, "--force-remove", 4)
                      or _is_long_flag(t, "--index-info", 4) for t in args)
                  if sub == "update-index" else "-w" in args)
        if writes:
            if any(t in ("--stdin", "--index-info", "--stdin-paths", "-z") or t.startswith("--index-info")
                   for t in args):
                return GIT_STAGE_REASON
            paths = [t.rsplit(",", 1)[-1] for t in _git_pathspecs(args, {"--cacheinfo", "-t", "--path",
                                                                          "--chmod"}) if t]
            cacheinfo = [args[i + 3] for i, t in enumerate(args) if t == "--cacheinfo" and i + 3 < len(args)
                         and "," not in args[i + 1]]
            cacheinfo += [args[i + 1].rsplit(",", 1)[-1] for i, t in enumerate(args)
                          if t == "--cacheinfo" and i + 1 < len(args) and "," in args[i + 1]]
            if (paths or cacheinfo) and (bool(trees) or _pathspecs_reach(paths + cacheinfo, base, protected)):
                return GIT_STAGE_REASON
    if sub is not None:
        count = clients_index_count(workspace)
        if count is None or count > 0:
            verbose = any(_is_long_flag(t, "--verbose", 3) if t.startswith("--") else _short_flag_has(t, "v", "")
                          for t in args)
            if sub != "status" or verbose:
                # None means git could not answer: a different fix than untracking.
                return GIT_UNKNOWN_REASON if count is None else GIT_TRACKED_REASON
    return ""


# Options for every git query Torque runs itself (the gate and doctor): no pager,
# no fsmonitor or untracked-cache helper, English messages, no GIT_* overrides.
GIT_QUERY_OPTIONS = ("--no-pager", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
                     "-c", "core.pager=cat")


def _git_run(base: Path, args: list[str]) -> tuple[int, str, str] | None:
    """Run a read-only git query in base: (exit code, stdout, stderr), or None when
    git could not run or timed out."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["LC_ALL"] = "C"
    _spend()
    left = _remaining()
    try:
        done = subprocess.run(["git", *GIT_QUERY_OPTIONS, "-C", str(base), *args],
                              capture_output=True, text=True, timeout=15 if left is None else min(15, left),
                              env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return done.returncode, done.stdout, done.stderr


def _git_output(base: Path, args: list[str]) -> str | None:
    """stdout of a read-only git query in base; None when git fails or is missing."""
    done = _git_run(base, args)
    return done[1] if done is not None and done[0] == 0 else None


def clients_index_count(workspace: Path) -> int | None:
    """How many files under workspace/clients/ are in git's index: 0 when the
    workspace is not in a repository, None when git errors, times out or is missing.
    initiatives/ is internal (not client-confidential) and is deliberately not
    counted here: counting it would lock the agent out of ordinary git commands
    (git log, git diff, git rm --cached) whenever an initiative file is tracked.
    Memoized per call (_index_cache), so repeated guarded-folder passes that ask
    about the same workspace spawn the git query once, not once per folder."""
    key = _cf(str(workspace)) if _deadline is not None else None
    if key is not None and key in _index_cache:
        return _index_cache[key]
    count = _clients_index_count(workspace)
    if key is not None:
        _index_cache[key] = count
    return count


def _clients_index_count(workspace: Path) -> int | None:
    if not workspace.is_dir():
        return 0
    probe = _git_run(workspace, ["rev-parse", "--is-inside-work-tree"])
    if probe is None:
        return None
    if probe[0] != 0:
        return 0 if "not a git repository" in probe[2].casefold() else None
    listed = _git_run(workspace, ["ls-files", "--", _CLIENT_SPEC])
    if listed is None or listed[0] != 0:
        return None
    return len(listed[1].splitlines())


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
        if raw.startswith(":") or _PARAM_RE.search(raw) or "`" in raw:
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
            if _is_long_flag(tok, "--include-untracked", 3) or _is_long_flag(tok, "--only-untracked", 3):
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
    if action == "push" and not any(_is_long_flag(t, "--pathspec-from-file", 12) for t in action_args):
        specs = _git_pathspecs(action_args, {"-m", "--message"})
    if specs:
        return _pathspecs_reach(specs, base, protected)
    return _toplevel_reaches(base, protected)


GIT_LISTING_REASON = ("git status --ignored and git ls-files --others/--ignored list the names of files under "
                      "client context." + NARROW_PATH_HINT)


def _git_listing_reaches(rest: list[str], clients: Path, cwd: Path) -> bool:
    """`git status --ignored` and `git ls-files -o/-i/--others/--ignored` print
    the names of files git does not track, clients/ among them. Blocked when
    their scope reaches clients/: status covers the whole repository unless
    pathspecs narrow it; ls-files covers the current directory (or -C) down."""
    try:
        base, trees, sub, args = _git_parse(rest, cwd)
    except (OSError, ValueError):
        return True
    options = args[:args.index("--")] if "--" in args else args
    if sub == "status":
        if not any(t.split("=", 1)[0] == "--ignored" and t != "--ignored=no" for t in options):
            return False
        specs = _git_pathspecs(args, set())
        if trees:
            return True
        return _pathspecs_reach(specs, base, [clients]) if specs else _toplevel_reaches(base, [clients])
    if sub == "ls-files":
        listing = any((_is_long_flag(t, "--others", 4) or _is_long_flag(t, "--ignored", 4)) if t.startswith("--")
                      else _short_flag_has(t, "oi", "xX") for t in options)
        if not listing:
            return False
        specs = _git_pathspecs(args, {"-x", "--exclude", "-X", "--exclude-from", "--exclude-per-directory",
                                      "--with-tree", "--format"}) or ["."]
        return bool(trees) or _pathspecs_reach(specs, base, [clients])
    return False


PATCH_REASON = ("applying a patch outside project/ (git apply, git am, patch), or one whose paths climb out "
                "with .. or are absolute, could overwrite workspace.json, the hook configuration or client "
                "files. Run it from project/ with a patch file whose paths stay inside it")
EXTRACT_REASON = ("extracting an archive at or above the workspace root, into clients/, or with absolute or "
                  "rewritten member names could overwrite workspace.json, the hook configuration or client "
                  "files. Extract into project/ or a folder under it")
# The most of a patch file the gate reads to check its paths.
_PATCH_READ_LIMIT = 5_000_000


def _patch_file_safe(raw: str, cwd: Path) -> bool:
    """True when a patch file can be read and every path it names is relative
    and has no `..` segment (/dev/null aside)."""
    try:
        path = _resolve(cwd, raw)
        if path.stat().st_size > _PATCH_READ_LIMIT:
            return False
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return False
    names: list[str] = []
    for line in text.splitlines():
        if line.startswith(("--- ", "+++ ")):
            names.append(line[4:].split("\t", 1)[0].strip())
        elif line.startswith("diff --git "):
            names += line[len("diff --git "):].split()
        elif line.startswith(("rename from ", "rename to ", "copy from ", "copy to ", "Index: ")):
            names.append(line.split(" ", 2)[-1].strip())
    for name in names:
        name = name.strip('"')
        if name == "/dev/null":
            continue
        if name.startswith(("/", "\\", "~")) or re.match(r"^[A-Za-z]:", name):
            return False
        if ".." in re.split(r"[/\\]", name):
            return False
    return True


def _git_patch_reason(rest: list[str], workspace: Path, cwd: Path) -> str:
    """git apply and git am write the files a patch names. git apply run inside
    project/ changes only paths under the current directory and rejects `..`
    and absolute paths; git am applies from the repository's top. So git apply
    must run inside project/ (or at the root with a --directory inside project/),
    and git am needs a repository whose top is inside project/. A named patch
    file must name no absolute path and no `..`; --unsafe-paths is blocked."""
    try:
        base, trees, sub, args = _git_parse(rest, cwd)
    except (OSError, ValueError):
        return PATCH_REASON if any(t in ("apply", "am") for t in rest) else ""
    if sub not in ("apply", "am"):
        return ""
    project = Path(os.path.realpath(str(workspace / "project")))
    if sub == "am":
        if args and all(t in ("--abort", "--quit", "--show-current-patch") or t.startswith("--show-current-patch=")
                        for t in args):
            return ""
        top = _git_toplevel(base)
        if top is None or not _is_within(top, project) or trees:
            return PATCH_REASON
        files = [t for t in args if not t.startswith("-")]
        return "" if all(_patch_file_safe(f, base) for f in files if os.path.isfile(_resolve(base, f))) else PATCH_REASON
    options = args[:args.index("--")] if "--" in args else args
    names = [t.split("=", 1)[0] for t in options if t.startswith("--")]
    if any(_is_long_flag(n, "--unsafe-paths", 4) for n in names):
        return PATCH_REASON
    writes = (any(_is_long_flag(n, "--apply", 4) or _is_long_flag(n, "--index", 4) or _is_long_flag(n, "--cached", 4)
                  for n in names)
              or not any(_is_long_flag(n, flag, 4) for n in names for flag in ("--check", "--stat", "--numstat",
                                                                                  "--summary")))
    if not writes:
        return ""
    directory = None
    files: list[str] = []
    j = 0
    while j < len(args):
        tok = args[j]
        name, eq, value = tok.partition("=")
        if tok == "--":
            files += args[j + 1:]
            break
        if tok.startswith("--") and _is_long_flag(name, "--directory", 5):
            directory = value if eq else (args[j + 1] if j + 1 < len(args) else "")
            j += 0 if eq else 1
        elif tok.startswith("--") and not eq and name in ("--exclude", "--include", "--whitespace",
                                                          "--build-fake-ancestor"):
            j += 1
        elif tok in ("-p", "-C"):
            j += 1
        elif not tok.startswith("-"):
            files.append(tok)
        j += 1
    if directory is not None:
        if not directory or directory.startswith(("/", "\\", "~")) or re.match(r"^[A-Za-z]:", directory) \
                or ".." in re.split(r"[/\\]", directory) or _PARAM_RE.search(directory):
            return PATCH_REASON
        try:
            confined = _is_within(_resolve(base, directory), project)
        except (OSError, ValueError):
            return PATCH_REASON
    else:
        confined = _is_within(base, project)
    if not confined or trees:
        return PATCH_REASON
    if any(f != "-" and not _patch_file_safe(f, base) for f in files):
        return PATCH_REASON
    return ""


def _patch_reason(toks: list[str], words: list[str], index: int, workspace: Path, cwd: Path) -> str:
    """`patch` writes the files its patch names, relative to its directory
    (-d/--directory, else the current one). It must run inside project/, and
    its patch must come from a file the gate can read (-i, --input, a `<` file
    or the second operand) whose paths are relative with no `..`. A dry run
    writes nothing."""
    rest = words[index + 1:]
    if any(t in ("--dry-run", "-C", "--check") for t in rest):
        return ""
    directory = None
    source = None
    operands: list[str] = []
    j = 0
    while j < len(rest):
        tok = rest[j]
        name, eq, value = tok.partition("=")
        if tok == "--":
            operands += rest[j + 1:]
            break
        if tok.startswith("--"):
            if name in ("--directory", "--input"):
                val = value if eq else (rest[j + 1] if j + 1 < len(rest) else "")
                j += 0 if eq else 1
                if name == "--directory":
                    directory = val
                else:
                    source = val
            elif not eq and name in ("--strip", "--output", "--reject-file", "--backup", "--prefix", "--suffix",
                                     "--basename-prefix", "--fuzz", "--version-control", "--ifdef", "--quoting-style",
                                     "--get", "--reject-format", "--read-only"):
                j += 1
        elif tok.startswith("-") and len(tok) > 1:
            letter = tok[1]
            if letter in "dioprBDFVYzg":
                val = tok[2:] or (rest[j + 1] if j + 1 < len(rest) else "")
                if not tok[2:]:
                    j += 1
                if letter == "d":
                    directory = val
                elif letter == "i":
                    source = val
        else:
            operands.append(tok)
        j += 1
    if source is None and len(operands) >= 2:
        source = operands[1]
    if source is None:
        for k, tok in enumerate(toks):
            m = _REDIRECT_RE.match(tok)
            if m and tok.lstrip("0123456789").startswith("<") and not tok.lstrip("0123456789").startswith(("<<", "<&", "<>")):
                source = m.group("rest") or (toks[k + 1] if k + 1 < len(toks) else "")
    if not source or source == "-":
        return PATCH_REASON
    project = Path(os.path.realpath(str(workspace / "project")))
    try:
        base = _resolve(cwd, directory) if directory else cwd
    except (OSError, ValueError):
        return PATCH_REASON
    if (directory and _PARAM_RE.search(directory)) or not _is_within(base, project):
        return PATCH_REASON
    return "" if _patch_file_safe(source, cwd) else PATCH_REASON


def _tar_extracts(rest: list[str]) -> bool:
    if rest and not rest[0].startswith("-") and _TAR_MODE_RE.match(rest[0]) and "x" in rest[0]:
        return True
    for tok in rest:
        if tok == "--":
            break
        if tok.startswith("--"):
            name = tok.split("=", 1)[0]
            if _is_long_flag(name, "--extract", 5) or _is_long_flag(name, "--get", 4):
                return True
        elif _short_flag_has(tok, "x", "".join(sorted(_TAR_VALUE_LETTERS))):
            return True
    return False


def _tar_extract_reason(head: str, rest: list[str], clients: Path, cwd: Path) -> str:
    """tar/bsdtar extraction writes under the current directory and each -C
    directory. Blocked when one of those is the workspace root, above it or in
    clients/, and for absolute member names (-P) or rewritten names
    (--transform, -s)."""
    if not _tar_extracts(rest):
        return ""
    for tok in rest:
        if tok == "--":
            break
        name = tok.split("=", 1)[0]
        if tok.startswith("--"):
            if (_is_long_flag(name, "--absolute-names", 4) or _is_long_flag(name, "--transform", 4)
                    or _is_long_flag(name, "--xform", 3)):
                return EXTRACT_REASON
        elif _short_flag_has(tok, "Ps" if head == "bsdtar" else "P", "".join(sorted(_TAR_VALUE_LETTERS))):
            return EXTRACT_REASON
    if rest and not rest[0].startswith("-") and _TAR_MODE_RE.match(rest[0]) and "P" in rest[0]:
        return EXTRACT_REASON
    dirs: list[Path] = []
    operands = _tar_operands(rest, cwd, dirs)
    if operands is None:
        return EXTRACT_REASON
    # Members extract into the directory in effect: the current one when no -C
    # is given, or when words besides the archive come before the first -C.
    if not dirs or sum(1 for _, base in operands if base == cwd) > 1:
        dirs.append(cwd)
    return EXTRACT_REASON if any(_reaches(d, clients) for d in dirs) else ""


def _short_option_values(rest: list[str], value_letters: str) -> tuple[list[tuple[str, str]], str]:
    """Read short-option clusters the way getopt does: in `-qod..` or `-qod ..`
    the first letter that takes a value takes the rest of the word, or the next
    word when it ends the word. Returns ([(letter, value)], every flag letter
    seen before a value letter). Words after `--` are not options."""
    values: list[tuple[str, str]] = []
    flags = ""
    j = 0
    while j < len(rest):
        tok = rest[j]
        if tok == "--":
            break
        if tok.startswith("-") and not tok.startswith("--") and len(tok) > 1:
            for index, letter in enumerate(tok[1:], start=1):
                if letter in value_letters:
                    value = tok[index + 1:]
                    if not value:
                        value = rest[j + 1] if j + 1 < len(rest) else ""
                        j += 1
                    values.append((letter, value))
                    break
                flags += letter
        j += 1
    return values, flags


# unzip's options that take a value: -d EXDIR, -P PASSWORD, -I/-O character sets.
_UNZIP_VALUE_LETTERS = "dPIO"


def _unzip_reason(rest: list[str], clients: Path, cwd: Path, workspace: Path) -> str:
    """unzip writes under -d EXDIR (in any cluster: -d DIR, -dDIR, -od.., -qod ..),
    else the current directory. Listing, testing and printing modes (-l, -t, -p,
    -c, -Z) write nothing; -: keeps `..` in member names."""
    values, flags = _short_option_values(rest, _UNZIP_VALUE_LETTERS)
    if ":" in flags:
        return EXTRACT_REASON
    if re.search(r"[ltpcZ]", flags):
        return ""
    targets = [value for letter, value in values if letter == "d"] or ["."]
    if any(not target for target in targets):
        return EXTRACT_REASON
    return EXTRACT_REASON if any(_root_reaches(target, clients, cwd, workspace) for target in targets) else ""


def _ditto_reason(rest: list[str], clients: Path, cwd: Path, workspace: Path) -> str:
    """ditto -x extracts an archive into its last operand."""
    if not any(_short_flag_has(t, "x") for t in rest):
        return ""
    operands = [t for t in rest if not t.startswith("-")]
    if len(operands) < 2:
        return EXTRACT_REASON
    return EXTRACT_REASON if _root_reaches(operands[-1], clients, cwd, workspace) else ""


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


def _block_command(toks: list[str], clients: Path, claude_dir: Path, workspace: Path, cwd: Path,
                   org_rules: bool = True) -> str:
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
    if _is_git_rm_cached_of_clients(toks, words, clients, cwd):
        # The way out of a tracked clients/: it only removes index entries.
        return ""
    for i, tok in enumerate(words):
        rest = words[i + 1:]
        if _GIT_CORE_PROGRAM_RE.match(_basename(tok)) and re.search(r"[/\\]", tok):
            return ("git's own programs (git-add, git-diff, ...) run from the git-core directory skip the "
                    "checks on git; use git")
        if _basename(tok) == "git":
            reason = _git_location_reason(rest, words[:i], workspace, cwd)
            if reason:
                return reason
        if _is_sf_token(tok) and not _sf_command_position(words, i):
            # A bare `sf`/`sfdx`/`@salesforce/cli` word that is an argument to another
            # command (which sf, rg sfdx, npm ls @salesforce/cli) is not an sf call.
            pass
        elif _is_sf_token(tok) and (_sf_is_help(rest) or _sf_is_version(rest)):
            # oclif handles help and the global version flag before it runs a command,
            # so these reach no org and read no tree, in either mode. (Connected mode's
            # own classifier still refuses a credential command whatever its flags.)
            pass
        elif _is_sf_token(tok) and not org_rules:
            if _sf_tree_reader_reaches(rest, clients, cwd, workspace):
                return (f"{_basename(tok) or 'sf'} {' '.join(rest[:2])} would read client context."
                        + NARROW_PATH_HINT)
        elif _is_sf_token(tok):
            topic = _sf_topic(rest)
            if _sf_has_org_flag(rest, topic):
                return f"{_basename(tok) or 'sf'} {' '.join(rest[:2])} can reach a Salesforce org"
            if not _sf_is_local(rest):
                return f"{_basename(tok) or 'sf'} {' '.join(rest[:2])} can reach a Salesforce org"
            if _sf_tree_reader_reaches(rest, clients, cwd, workspace):
                return (f"{_basename(tok) or 'sf'} {' '.join(rest[:2])} would read client context."
                        + NARROW_PATH_HINT)
        elif _basename(tok) == "git" and _git_reaches(rest, clients, cwd, workspace):
            return "git grep or git diff over untracked files can read client context." + NARROW_PATH_HINT
        elif _basename(tok) == "git" and _git_wipe_reaches(rest, words, clients, claude_dir, cwd):
            return GIT_WIPE_REASON
        elif _basename(tok) == "git" and _git_listing_reaches(rest, clients, cwd):
            return GIT_LISTING_REASON
        elif _basename(tok) == "git" and _git_patch_reason(rest, workspace, cwd):
            return PATCH_REASON
        elif _basename(tok) == "git" and (git_reason := _git_stage_reason(rest, clients, claude_dir, cwd, workspace)):
            # Bind the reason once (it is computed here on the deny path only).
            return git_reason
        elif _INSTALLER_RE.match(_basename(tok)):
            reason = _installer_reason(_basename(tok), rest)
            if reason:
                return reason
        elif _basename(tok) in CONSOLE_SCRIPTS and not org_rules:
            continue
        elif _basename(tok) in CONSOLE_SCRIPTS:
            name = _basename(tok)
            reason = _torque_reason(rest) if CONSOLE_SCRIPTS[name] == "torque" else _delegate_reason(name, rest)
            if reason:
                return reason
        elif PY_LAUNCHER_RE.match(_basename(tok)) and _python_module(rest)[0]:
            module, after = _python_module(rest)
            if not org_rules and (module in TORQUE_MAIN_MODULES or DELEGATE_MODULE_RE.match(module)):
                continue
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
        elif _basename(tok) == "ln":
            if _ln_reaches(rest, clients, cwd, workspace):
                return LN_REASON
        elif _basename(tok) == "mklink":
            if _mklink_reaches(rest, clients, cwd, workspace):
                return LN_REASON
        elif _basename(tok) == "new-item":
            if _new_item_reaches(rest, clients, cwd, workspace):
                return LN_REASON
        elif _basename(tok) == "patch":
            reason = _patch_reason(toks, words, i, workspace, cwd)
            if reason:
                return reason
        elif _basename(tok) == "unzip":
            reason = _unzip_reason(rest, clients, cwd, workspace)
            if reason:
                return reason
        elif _basename(tok) == "ditto":
            reason = _ditto_reason(rest, clients, cwd, workspace)
            if reason:
                return reason
        elif _basename(tok) in TAR_HEADS:
            reason = _tar_extract_reason(_basename(tok), rest, clients, cwd)
            if reason:
                return reason
            operands = _tar_operands(rest, cwd)
            if operands is None or any(_root_reaches(raw, clients, base, workspace) for raw, base in operands):
                return "this command can search client context recursively." + NARROW_PATH_HINT
        elif _is_recursive_search(tok, rest):
            head = _basename(tok)
            if _recursive_search_reaches(head, rest, clients, cwd, workspace):
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
            reason = _block_command(rest_toks, clients, claude_dir, workspace, base, org_rules)
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


_GIT_CORE_PROGRAM_RE = re.compile(r"^git-[a-z0-9][a-z0-9-]*$")
_GIT_RM_OPTIONS = {"--cached", "-r", "-q", "--quiet", "-f", "--force", "--ignore-unmatch", "-rf", "-fr", "--"}


def _is_git_rm_cached_of_clients(toks: list[str], words: list[str], clients: Path, cwd: Path) -> bool:
    """`git rm --cached [-r -q -f] PATHS` where every path is under clients/ and
    nothing else is on the line."""
    if toks != words or len(words) < 4 or _basename(words[0]) != "git" or words[1] != "rm":
        return False
    args = words[2:]
    options = [t for t in args if t.startswith("-")]
    paths = [t for t in args if not t.startswith("-")]
    if "--cached" not in options or not set(options) <= _GIT_RM_OPTIONS or not paths:
        return False
    if any(_has_glob_char(p) or p.startswith(":") for p in paths):
        return False
    try:
        return all(_is_within(_resolve(cwd, p), clients) for p in paths)
    except (OSError, ValueError):
        return False


def _git_location_reason(rest: list[str], before: list[str], workspace: Path, cwd: Path) -> str:
    """git run against another repository or work tree (-C, --git-dir, --work-tree,
    -c core.worktree=, GIT_DIR=, GIT_WORK_TREE=) must stay inside project/: the
    gate's checks read the workspace's own repository."""
    project = Path(os.path.realpath(str(workspace / "project")))
    targets: list[tuple[str, Path]] = []
    for word in before:
        name, eq, value = word.partition("=")
        if eq and name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
            targets.append((value, cwd))
    base = cwd
    j = 0
    while j < len(rest):
        tok = rest[j]
        name, eq, value = tok.partition("=")
        if tok == "-C" and j + 1 < len(rest):
            targets.append((rest[j + 1], base))
            try:
                base = _resolve(base, rest[j + 1])
            except (OSError, ValueError):
                return "git -C with a directory the gate cannot resolve"
            j += 2
            continue
        if name in ("--git-dir", "--work-tree"):
            if not eq:
                value = rest[j + 1] if j + 1 < len(rest) else ""
                j += 1
            targets.append((value, base))
        elif tok == "-c" and j + 1 < len(rest):
            key, _, val = rest[j + 1].partition("=")
            if key.casefold() == "core.worktree":
                targets.append((val, base))
            j += 1
        elif not tok.startswith("-"):
            break
        j += 1
    for raw, where in targets:
        if not raw or "$" in raw or "`" in raw:
            return "git pointed at a repository or work tree the gate cannot resolve"
        try:
            target = _resolve(where, raw)
        except (OSError, ValueError):
            return "git pointed at a repository or work tree the gate cannot resolve"
        if not _is_within(target, project):
            return ("git -C, --git-dir, --work-tree, core.worktree, GIT_DIR and GIT_WORK_TREE must stay inside "
                    "project/ in this mode. Run git from the directory instead")
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
        return "this command targets workspace.json, the hook configuration, or consent and approval records"
    if _token_targets_claude_dir(tok, claude_dir, cwd):
        return "this command targets the .claude hook configuration directory"
    if write and _token_names_agents_dir(tok, workspace, cwd):
        return "this command targets the .agents folder, which holds the Antigravity hook configuration"
    if _token_targets_package(tok, cwd):
        return "this command targets the installed Torque package that enforces this mode"
    if _token_is_client_path(tok, clients, cwd, workspace):
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
        expanded = _expand_braces_once(command)
        if expanded == command:
            break
        command = expanded
    return command


def _expand_braces_once(command: str) -> str:
    """One left-to-right pass: each {a,b} group with the run of word characters
    before it (back to the end of the previous expanded word) and after it. This
    gives the same result as matching prefix{a,b}suffix with one regular
    expression, in linear time: each character is scanned a bounded number of times."""
    out: list[str] = []
    pos = total = 0
    size = len(command)
    for match in _BRACE_GROUP_RE.finditer(command):
        if match.start() < pos:
            continue
        start = match.start()
        while start > pos and command[start - 1] not in _BRACE_WORD_STOP:
            start -= 1
        end = match.end()
        while end < size and command[end] not in _BRACE_WORD_STOP:
            end += 1
        prefix, suffix = command[start:match.start()], command[match.end():end]
        out.append(command[pos:start])
        out.append(" ".join(prefix + alt + suffix for alt in match.group(1).split(",")))
        total += len(out[-2]) + len(out[-1])
        if total > MAX_EXPANDED_CHARS:
            raise ValueError(f"brace expansion makes this command longer than {MAX_EXPANDED_CHARS:,} characters")
        pos = end
    if not out:
        return command
    out.append(command[pos:])
    return "".join(out)


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
    while j < len(toks) and (toks[j] in _CD_PREFIXES or toks[j] in _SHELL_KEYWORDS or _ASSIGN_RE.match(toks[j])):
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
               _depth: int = 0, org_rules: bool = True) -> str:
    """Check every simple command that command can run, as the command reader
    (_read_commands) finds them, against each directory it could run in."""
    if _depth == 0 and len(command) > MAX_INPUT_CHARS:
        # decide() blocks this first; kept here so no caller parses an over-long command.
        return _too_long(len(command))
    for kind, item in _read_commands(command, workspace, cwd, _depth):
        if kind == "error":
            return item
        if kind == "text":
            text, start = item
            if _STASH_UNTRACKED_REF_RE.search(text) and re.search(r"(?<![A-Za-z0-9_.-])git(?![A-Za-z0-9_-])", text):
                # The segment splitter cuts stash@{0}^3 at its braces, so a stash's
                # untracked parent is looked for in the whole command.
                protected = _git_protected(clients, claude_dir)
                if any(_toplevel_reaches(base, protected) for base in start):
                    return GIT_WIPE_REASON
        elif kind == "segment":
            toks, _sep, cwds, _unknown = item
            for here in cwds:
                reason = _block_command(toks, clients, claude_dir, workspace, here, org_rules) if toks else ""
                if reason:
                    return reason
    return ""


def _read_commands(command: str, workspace: Path, cwd: Path | list[Path], _depth: int = 0, unknown: bool = False):
    """The command reader that build-only's scan and the every-mode record check
    share. It yields, in order: ("text", (text, start directories)) for each text
    the shell can run, as normalized below; ("segment", (words, separator after
    them, directories, unknown)) for each of its simple commands; ("end", None)
    after each text; and ("error", reason) for a text it cannot read.

    A text is read with $HOME, ~ and $PWD filled in, ANSI-C quoting decoded, any
    other ${...} kept as one unknown word and brace expansion done. It is also
    read with each zsh glob group as a wildcard, and with what its variables
    could hold written in (_values_reading). The texts it hands to a shell are
    read as well: the string of bash -c / sh -c / zsh -c and the words of eval
    (_nested_commands), and $(...) / backtick substitutions (already exposed as
    their own segments by the paren/backtick splitter, and read again here whole).

    A cd, pushd or popd earlier in the command changes the directory later
    relative paths resolve against. Each segment comes with every directory it
    could run in: right after `cd X &&` that is X alone; after any other
    separator (;, ||, |, &, a newline) the cd may have failed or run in a
    subshell, so the earlier directories stay possible too. When the command has
    grouping ((), {}, backticks), every directory seen stays possible. After a
    directory change whose target cannot be known here (cd -, cd ~-, popd,
    cd "$VAR", cd "$(...)"), the rest of the command comes with every directory
    seen, the starting directories and all of their parents, and `unknown` set."""
    if _depth > 8 or not command:
        return
    entry = unknown
    command = _expand_home_in_command(command)
    command = _decode_ansi_c(command)
    command = _PWD_BRACED_RE.sub("$PWD", command)
    values = _values_reading(command)
    values = values if values != command else ""
    # Any other ${...} (${R}, ${PWD%/project}) is unknown here. Keep it one word,
    # $0, so the splitter below does not cut it at its braces.
    command = _BRACED_PARAM_RE.sub("$0", command)
    try:
        command = _expand_braces(command)
    except ValueError as exc:
        yield "error", str(exc)
        return
    start = list(cwd) if isinstance(cwd, list) else [cwd]
    yield "text", (command, start)
    globbed = _WORD_GROUP_RE.sub("*", command)
    if globbed != command:
        # zsh glob groups and qualifiers (c(l)ients, notes(.)) and comma-less
        # brace groups (c{l..l}ients) may match protected paths: read the
        # command again with each group read as a wildcard.
        yield from _read_commands(globbed, workspace, cwd, _depth + 1, entry)
    seen: list[Path] = list(start)
    current: list[Path] = list(start)
    grouped = any(c in command for c in _GROUPING_CHARS)
    for toks, sep in _segments_with_separators(command):
        yield "segment", (toks, sep, current, unknown)
        for inner in _nested_commands(toks):
            yield from _read_commands(inner, workspace, list(current), _depth + 1, unknown)
        try:
            launched = _powershell_launched(toks)
        except ValueError as exc:
            yield "error", str(exc)
            launched = []
        for inner in launched:
            yield from _read_commands(inner, workspace, list(current), _depth + 1, unknown)
        target = _cd_target(toks)
        if target is not None and sep in ("`", "("):
            # cd `...` or cd $(...): the splitter cut the target off.
            target = ""
        if target == "":
            seen = _widened_cwds(seen, start, workspace)
            current = list(seen)
            unknown = True
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
    # command once more, quote-aware, and read each -c string intact.
    try:
        whole = shlex.split(command, posix=True)
    except ValueError:
        whole = []
    for inner in _nested_commands(whole):
        yield from _read_commands(inner, workspace, list(seen), _depth + 1, unknown)
    try:
        launched = _powershell_launched(whole)
    except ValueError as exc:
        yield "error", str(exc)
        launched = []
    for inner in launched:
        yield from _read_commands(inner, workspace, list(seen), _depth + 1, unknown)
    for nested in _direct_substitutions(command):
        yield from _read_commands(nested, workspace, list(seen), _depth + 1, unknown)
    if values:
        yield from _read_commands(values, workspace, cwd, _depth + 1, entry)
    yield "end", None


# ${NAME:-word}, ${NAME-word}, ${NAME:=word}, ${NAME=word}, ${NAME:+word} and
# ${NAME+word}: the shell can put the word in their place.
_PARAM_WORD_RE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:?[-=+]([^{}]*)\}")
# An assignment with a value that holds nothing to expand: NAME=value in Bash and
# $NAME = value in PowerShell (as _powershell_as_bash writes it), the value plain or
# in quotes; and a later $NAME or ${NAME}.
_KNOWN_VALUE_RE = re.compile(
    r"(?:^|(?<=[\s;&|(){}`'\"]))(?P<name>[A-Za-z_][A-Za-z0-9_]*)="
    r"(?P<value>'[^']*'|\"[^\"$`]*\"|[^\s;&|(){}<>`'\"$\\]*)(?=[\s;&|(){}<>`'\"]|$)"
    r"|\$(?P<psname>[A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*"
    r"(?P<psvalue>'[^']*'|\"[^\"$`]*\"|[^\s;&|(){}<>`'\"$\\=][^\s;&|(){}<>`'\"$\\]*)"
    r"|\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|(?P<plain>[A-Za-z_][A-Za-z0-9_]*)(?![A-Za-z0-9_]))")
# A value written in as it is: no space, quote or character the reader splits at.
_PLAIN_VALUE_RE = re.compile(r"[\w./~:@%+,=*?\[\]-]*")


def _values_reading(command: str) -> str:
    """command with what its variables could hold written in: the word of
    ${NAME:-word} (and the -, =, :=, + and :+ forms), and for $NAME or ${NAME} the
    value an earlier assignment in the same command gave it (C=clients/acme; rm
    $C/x). The command as written is read too; this reading only adds what the
    shell could make of those words. A value with a space, a quote or anything to
    expand is left unknown."""
    text = _PARAM_WORD_RE.sub(lambda m: m.group(1), command)
    known: dict[str, str] = {}

    def fill(m: re.Match) -> str:
        name = m.group("name") or m.group("psname")
        if name:
            raw = m.group("value") if m.group("name") else m.group("psvalue")
            value = raw[1:-1] if raw[:1] in ("'", '"') and len(raw) > 1 else raw
            if os.name == "nt":
                # Windows reads a backslash as a separator, and the splitter would eat it.
                value = value.replace("\\", "/")
            if _PLAIN_VALUE_RE.fullmatch(value):
                known[name] = value
            else:
                known.pop(name, None)
            return m.group(0)
        return known.get(m.group("braced") or m.group("plain"), m.group(0))
    return _KNOWN_VALUE_RE.sub(fill, text)


def _powershell_launched(toks: list[str]) -> list[str]:
    """The commands a powershell or pwsh started from a shell runs, read as the
    build-only scan reads a PowerShell tool call (_powershell_commands): the text of
    -Command or -c, and a decoded -EncodedCommand. A -File script's path is a word of
    the line and is checked as one. ValueError when an encoded command cannot be read."""
    found: list[str] = []
    for i, tok in enumerate(toks):
        if _basename(tok) in ("powershell", "pwsh") and i + 1 < len(toks):
            for text in _powershell_commands(shlex.join(toks[i:]))[1:]:
                if text not in found:
                    found.append(text)
    return found


def _nested_commands(toks: list[str]) -> list[str]:
    """The command strings a segment hands to a shell to run: the string of
    bash -c / sh -c / zsh -c (_shell_c_strings), and the words after eval, which
    eval joins with spaces and runs as a command."""
    found = _shell_c_strings(toks)
    found += [" ".join(toks[i + 1:]) for i, tok in enumerate(toks) if _basename(tok) == "eval" and i + 1 < len(toks)]
    return found


_SHELL_C_FLAG_RE = re.compile(r"^-[a-zA-Z]*c[a-zA-Z]*$")


# The letter escapes of ANSI-C ($'...') quoting, as Bash decodes them.
_ANSI_C = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v",
           "\\": "\\", "'": "'", '"': '"', "?": "?"}


def _ansi_c(text: str) -> str:
    """The text of a $'...' word as Bash decodes it: the letter escapes, one to three
    octal digits, \\x with one or two hex digits, \\u with up to four and \\U with up
    to eight, \\cX; an escape Bash does not know keeps its backslash; and a NUL ends
    the text, as it does in Bash. The one Bash-compatible decoder, which build-only's
    _decode_ansi_c and connected_routes both use, so the two gates agree with bash
    (a trailing invalid escape no longer makes the build-only gate fail open)."""
    out, i = [], 0
    while i < len(text):
        c = text[i]
        follower = text[i + 1:i + 2]
        if c != "\\" or not follower:
            out.append(c.encode("utf-8", "surrogateescape"))
            i += 1
        elif follower in _ANSI_C:
            out.append(_ANSI_C[follower].encode("utf-8"))
            i += 2
        elif follower in "01234567":
            digits = re.match(r"[0-7]{1,3}", text[i + 1:]).group()
            out.append(bytes([int(digits, 8) & 0xFF]))
            i += 1 + len(digits)
        elif follower in "xuU" and re.match(r"[0-9A-Fa-f]", text[i + 2:i + 3]):
            digits = re.match(r"[0-9A-Fa-f]{1,%d}" % {"x": 2, "u": 4, "U": 8}[follower], text[i + 2:]).group()
            value = int(digits, 16)
            if follower == "x":
                out.append(bytes([value]))
            elif value < 0x110000:
                out.append(chr(value).encode("utf-8", "surrogatepass"))
            elif value < 0x80000000:
                # Bash still writes the older five-byte and six-byte UTF-8 forms.
                length = 4 if value < 0x200000 else 5 if value < 0x4000000 else 6
                prefix = {4: 0xF0, 5: 0xF8, 6: 0xFC}[length]
                out.append(bytes([prefix | value >> (6 * (length - 1))] +
                                 [0x80 | (value >> shift) & 0x3F
                                  for shift in range(6 * (length - 2), -1, -6)]))
            i += 2 + len(digits)
        elif follower == "c" and text[i + 2:i + 3]:
            # Bash controls the first byte, then keeps the rest of a UTF-8 character.
            value = text[i + 2].encode("utf-8", "surrogateescape")
            control = 0x7F if value[0] == 0x3F else value[0] & 0x1F
            out.append(bytes([control]) + value[1:])
            i += 4 if text[i + 2:i + 4] == "\\\\" else 3
        else:
            out.append((c + follower).encode("utf-8", "surrogateescape"))
            i += 2
    return b"".join(out).split(b"\0", 1)[0].decode("utf-8", "surrogateescape")


def _decode_ansi_c(command: str) -> str:
    """Decode bash/zsh ANSI-C quoting ($'\\x63lients' is 'clients') into an ordinary
    single-quoted word, with _ansi_c (the Bash-compatible decoder), and drop the $ of
    locale quoting ($"...")."""
    return _ANSI_C_RE.sub(lambda m: shlex.quote(_ansi_c(m.group(1))), command).replace('$"', '"')


def _shell_c_strings(toks: list[str]) -> list[str]:
    """The command strings passed to bash/sh/zsh with -c (also -lc, -ec)."""
    found = []
    for i in range(len(toks) - 2):
        if _basename(toks[i]) in SHELL_HEADS and _SHELL_C_FLAG_RE.match(toks[i + 1]):
            found.append(toks[i + 2])
    return found


# PowerShell, as far as the Bash scan reads it. Its escape character is the backtick
# (never a substitution), its names ignore case, and these cmdlets and aliases are
# commands the scan already knows under another name.
_PS_HEADS = {"set-location": "cd", "sl": "cd", "chdir": "cd", "cd": "cd",
             "push-location": "pushd", "pushd": "pushd", "pop-location": "popd", "popd": "popd",
             "get-childitem": "ls", "gci": "ls", "dir": "ls", "ls": "ls",
             "copy-item": "cp", "cpi": "cp", "copy": "cp", "cp": "cp",
             "remove-item": "rm", "ri": "rm", "del": "rm", "erase": "rm", "rd": "rm", "rmdir": "rm", "rm": "rm",
             "move-item": "mv", "mi": "mv", "move": "mv", "rename-item": "mv", "rni": "mv", "ren": "mv",
             "mv": "mv", "tree": "tree", "findstr": "grep", "xcopy": "cp", "robocopy": "cp"}
# One of those names where a command can start, then the rest of its simple command.
_PS_HEAD_RE = re.compile(r"(?<![\w./\\$-])(" + "|".join(sorted(map(re.escape, _PS_HEADS), key=len, reverse=True))
                         + r")(?![\w./\\-])([^;|&\n(){}]*)", re.IGNORECASE)
# -Recurse and its abbreviations, its alias -s, and -Depth (which implies it).
_PS_RECURSE_RE = re.compile(r"(?<=\s)-(?:r(?:e(?:c(?:u(?:r(?:s(?:e)?)?)?)?)?)?|s|depth)(?=[\s:]|$)", re.IGNORECASE)
# A cmd.exe switch (/s, /q, /e:on) as its own word: /s and /e mean the whole tree.
_CMD_SWITCH_RE = re.compile(r"(?<=\s)/([a-z]{1,2})(?::\S*)?(?=\s|$)", re.IGNORECASE)
_CMD_TREE_HEADS = frozenset({"dir", "del", "erase", "findstr"})
# Names that run a string as a command: only a call that holds one has its quoted
# strings read as commands, so an ordinary message or query is left alone. The
# dot keeps a file extension out: sf.cmd is not cmd.
_PS_EVALUATOR_RE = re.compile(r"(?<![\w.-])(iex|invoke-expression|powershell|pwsh|cmd|invoke-command|icm|scriptblock"
                              r"|invokescript|addscript|start-process|saps|start-job|sajb|wsl)(?![\w-])", re.IGNORECASE)
_PS_ENCODED_FLAGS = frozenset({"-ec"} | {"-" + "encodedcommand"[:n] for n in range(1, 15)})
_PS_MAX_STRINGS = 64


# Get-ChildItem parameters, matched by case-insensitive prefix. -Recurse and -Depth
# are read separately (they turn on recursion); -Path/-LiteralPath name a root; the
# value params take a value that is not a root; the switches take none.
_PS_RECURSE_PARAM_RE = re.compile(r"-(?:r(?:e(?:c(?:u(?:r(?:s(?:e)?)?)?)?)?)?|s)$", re.IGNORECASE)
_PS_DEPTH_PARAM_RE = re.compile(r"-dep(?:t(?:h)?)?$", re.IGNORECASE)
_GCI_PATH_PARAMS = ("path", "literalpath")
_GCI_VALUE_PARAMS = ("filter", "include", "exclude", "attributes")
_GCI_SWITCH_PARAMS = ("force", "name", "file", "directory", "hidden", "readonly", "system",
                      "followsymlink", "usetransaction")


def _ps_listing_rest(arg_text: str) -> str | None:
    """The arguments of a Get-ChildItem/gci/dir/ls listing rewritten for the Bash
    scan's `ls`: -Recurse and -Depth become `--recursive`, -Path/-LiteralPath values
    and positional words become the roots, and -Filter/-Include/-Exclude/-Depth
    values (and other value parameters) are dropped so they are not read as paths.
    Returns the text after `ls` (with a leading space), or None for a cmd.exe form
    (`/s`, `/b`), which the caller rewrites the old way."""
    if _CMD_SWITCH_RE.search(arg_text):
        return None
    try:
        toks = shlex.split(arg_text, posix=True)
    except ValueError:
        return None
    roots: list[str] = []
    recursive = False
    i = 0
    while i < len(toks):
        tok = toks[i]
        if not tok.startswith("-") or tok == "-":
            roots.append(tok)
            i += 1
            continue
        head, _, attached = tok.partition(":")
        if _PS_RECURSE_PARAM_RE.fullmatch(head):
            recursive = True
        elif _PS_DEPTH_PARAM_RE.fullmatch(head):
            recursive = True
            if not attached and i + 1 < len(toks) and re.fullmatch(r"\d+", toks[i + 1]):
                i += 1
        else:
            name = head[1:].casefold()
            paths = [p for p in _GCI_PATH_PARAMS if p.startswith(name)]
            values = [p for p in _GCI_VALUE_PARAMS if p.startswith(name)]
            switches = [p for p in _GCI_SWITCH_PARAMS if p.startswith(name)]
            if paths and not values and not switches:
                if attached:
                    roots.append(attached)
                elif i + 1 < len(toks):
                    roots.append(toks[i + 1])
                    i += 1
            elif switches and not values and not paths:
                pass  # a switch takes no value
            elif not attached and i + 1 < len(toks) and not toks[i + 1].startswith("-"):
                # a value parameter (filter, include, exclude, ...) or an unknown one:
                # drop its value so it is not read as a path (the safe reading).
                i += 1
        i += 1
    if recursive:
        # A wildcard path under -Recurse/-Depth names files to match while the search
        # walks from the pattern's parent directory, so root at that parent (cwd for a
        # bare `*.md`): Get-ChildItem *.md -Recurse recurses the current directory.
        roots = [(os.path.dirname(r) or ".") if _has_glob_char(r) else r for r in roots]
    parts = (["--recursive"] if recursive else []) + roots
    return (" " + " ".join(shlex.quote(part) for part in parts)) if parts else ""


def _powershell_as_bash(command: str) -> str:
    """A PowerShell command line rewritten as far as the Bash scan needs: path
    separators as slashes, the backtick escape dropped (a backtick before a line
    end joins the lines), and the listing, copy, remove, move and directory
    cmdlets under the names the scan knows, with -Recurse (and cmd's /s and /e) as
    --recursive and cmd's other switches dropped, so they are not read as paths. A
    listing cmdlet (Get-ChildItem and its aliases) is read by _ps_listing_rest, so
    its -Filter/-Include/-Exclude/-Depth values are not mistaken for its root."""
    def simple(match: re.Match) -> str:
        name = match.group(1).casefold()
        if _PS_HEADS[name] == "ls":
            listing = _ps_listing_rest(match.group(2))
            if listing is not None:
                return "ls" + listing
        rest = _PS_RECURSE_RE.sub("--recursive", match.group(2))
        whole_tree = re.search(r"(?<=\s)/[se](?=\s|$)", rest, re.IGNORECASE)
        rest = _CMD_SWITCH_RE.sub(lambda m: "--recursive" if m.group(1).casefold() in ("s", "e") else "", rest)
        if whole_tree and name in _CMD_TREE_HEADS:
            # dir /s NAME, del /s NAME and findstr /s TEXT NAME look for NAME in every
            # folder below the current one, so the current folder is searched too.
            rest += " ."
        return _PS_HEADS[name] + rest
    text = re.sub(r"`\r?\n", " ", command.replace("\\", "/")).replace("`", "")
    return _PS_HEAD_RE.sub(simple, text)


def _powershell_commands(command: str) -> list[str]:
    """Everything a PowerShell tool call can run, each as text for the Bash scan:
    the command, a decoded -EncodedCommand, and, when the call names something that
    runs a string (Invoke-Expression, powershell, cmd /c, a script block built from
    text), every quoted string of more than one word, at any nesting. ValueError
    when that cannot be read."""
    found = [_powershell_as_bash(command)]
    evaluates = bool(_PS_EVALUATOR_RE.search(command))
    for text in found:
        try:
            words = shlex.split(text, posix=True)
        except ValueError:
            words = [single or double for single, double in re.findall(r"'([^']*)'|\"([^\"]*)\"", text)]
        for i, word in enumerate(words):
            inner = None
            if (word.casefold() in _PS_ENCODED_FLAGS and i + 1 < len(words)
                    and any(_basename(w) in ("powershell", "pwsh") for w in words[:i])):
                try:
                    inner = _powershell_as_bash(base64.b64decode(words[i + 1], validate=True).decode("utf-16-le"))
                except (ValueError, UnicodeError):
                    raise ValueError("an encoded PowerShell command could not be read") from None
            elif evaluates and re.search(r"\S\s+\S", word):
                inner = _powershell_as_bash(word)
            if inner and inner not in found:
                if len(found) >= _PS_MAX_STRINGS:
                    raise ValueError("this command holds more quoted strings than the gate reads")
                found.append(inner)
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


def _mcp_surface(tool_name: str) -> str:
    """"desktop" or "browser" when an MCP tool name (mcp__<server>__<tool>) drives
    the desktop or a browser, otherwise "". Read from the server's name, and for a
    browser from the tool's own name too: Antigravity's browser tools arrive as
    mcp__antigravity__open_browser_url, mcp__antigravity__browser_click_element
    and so on (gate_antigravity)."""
    parts = tool_name.split("__")
    server, tool = "__".join(parts[1:-1]), parts[-1]
    if DESKTOP_SERVER.search(server):
        return "desktop"
    if BROWSER_SERVER.search(server) or "browser" in tool.casefold():
        return "browser"
    return ""


def _mcp_names(tool_name: str, tool_input: object) -> list[str]:
    """The names to classify an MCP call by: its own, and one per server it names
    in an argument. A resource read (ReadMcpResourceTool, Antigravity's
    read_resource) carries its server there, not in the tool name."""
    names = [tool_name]
    tool = tool_name.split("__")[-1]
    if isinstance(tool_input, dict) and "resource" in tool.casefold():
        for key, value in tool_input.items():
            if isinstance(key, str) and key.casefold() in _MCP_SERVER_KEYS and isinstance(value, str) and value:
                names.append(f"mcp__{value}__{tool}")
    return names


def _argument_names(value: object, depth: int = 0):
    """Every string a tool_input uses as an argument name (a dict key), nested."""
    if depth > 8:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                yield key
            yield from _argument_names(item, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _argument_names(item, depth + 1)


def _salesforce_host(tool_input: object) -> str:
    """The first Salesforce org or login host a string argument names (a value or an
    argument name, at any depth, percent escapes decoded), or "". Salesforce's
    public sites do not count."""
    for raw in itertools.chain(_string_values(tool_input), _argument_names(tool_input)):
        for text in dict.fromkeys([raw, urllib.parse.unquote(raw)]):
            for match in _SF_HOST_RE.finditer(text):
                host = match.group(1).casefold()
                if host not in SF_PUBLIC_HOSTS:
                    return host
    return ""


def _mcp_org_reason(tool_name: str, tool_input: dict) -> str:
    """Why build-only mode blocks an MCP call as org access, or "": its server or tool
    name indicates Salesforce, it drives a browser or the desktop, or a string
    argument names a Salesforce org or login host (a server under a neutral name)."""
    names = _mcp_names(tool_name, tool_input)
    if any(_mcp_reaches_salesforce(name) for name in names):
        return ("Build-only mode: this MCP tool looks like Salesforce org access. "
                "Disable Salesforce MCP servers in a build-only workspace.")
    if any(_mcp_surface(name) for name in names):
        return BROWSER_TOOL_REASON
    host = _salesforce_host(tool_input)
    if host:
        return (f"Build-only mode: this MCP tool call names the Salesforce host {host}, which is org access. "
                "Disable that MCP server in a build-only workspace, or run the step yourself outside the "
                "AI session.")
    return ""


def _worktree_copies(workspace: Path) -> list[Path]:
    """The worktrees under the workspace's .claude/worktrees/, resolved. Each may
    hold a copy of clients/ (tracked client files, or files .worktreeinclude
    names), so each is checked as a workspace of its own. Only a folder that is
    not there means no copies: any other error reading it is raised, which blocks
    the call (fail closed), since a copy that cannot be listed cannot be guarded."""
    folder = workspace.joinpath(*WORKTREES_DIR)
    try:
        entries = sorted(p for p in folder.iterdir() if p.is_dir())
    except (FileNotFoundError, NotADirectoryError):
        return []
    copies = [Path(os.path.realpath(str(p))) for p in entries]
    return [c for c in dict.fromkeys(copies) if _cf(str(c)) != _cf(str(workspace))]


def decide(tool_name: str, tool_input: dict, workspace: Path, mode: str,
           cwd: Path | None = None) -> tuple[bool, str]:
    if mode != "build-only":
        return True, ""
    reason = _oversized_reason(tool_name, tool_input)
    if reason:
        return False, reason
    with _call_budget():
        try:
            return _decide(tool_name, tool_input, workspace, cwd)
        except BudgetExceeded as exc:
            return False, _budget_reason(exc)


def _too_long(length: int) -> str:
    return (f"this call holds a string of {length:,} characters, longer than the "
            f"{MAX_INPUT_CHARS:,} characters the gate reads")


def _oversized_reason(tool_name: str, tool_input: dict) -> str:
    """Block, before any pattern runs, a call holding a string the gate would parse
    that is longer than MAX_INPUT_CHARS: a command, a path, or any argument of a
    tool whose arguments are all read as possible paths. The contents a file tool
    writes (Write, Edit, MultiEdit, NotebookEdit) and the arguments of tools that
    pass unchecked are not parsed, so they are not limited."""
    if tool_name in SAFE_TOOLS:
        return ""
    key = PATH_TOOLS.get(tool_name)
    if key:
        values = [tool_input.get(key), *(tool_input.get(k) for k in COMMAND_KEYS)]
    else:
        values = itertools.chain(_string_values(tool_input), _command_strings(tool_input))
    longest = max((len(v) for v in values if isinstance(v, str)), default=0)
    if longest <= MAX_INPUT_CHARS:
        return ""
    return (f"Build-only mode: {_too_long(longest)}; blocking to fail closed. Split the command, "
            "or put the long text in a file under project/ and pass the file.")


class _call_budget:
    """Start the time and glob budgets for one call, unless a call (main) has
    already started them; the outermost user clears them."""

    def __enter__(self):
        global _deadline, _glob_matches
        self.owner = _deadline is None
        if self.owner:
            _deadline = time.monotonic() + GATE_TIME_BUDGET
            _glob_matches = 0
            _glob_cache.clear()
            _index_cache.clear()
        return self

    def __exit__(self, *exc):
        global _deadline, _glob_matches
        if self.owner:
            _deadline = None
            _glob_matches = 0
            _glob_cache.clear()
            _index_cache.clear()
        return False


def _watchdog_fire() -> None:
    """The hard end of the time budget: whatever the gate is doing (a glob inside
    the standard library, a slow disk), block the call and end the process."""
    try:
        sys.stderr.write(_budget_reason(BudgetExceeded(
            f"the gate's {GATE_TIME_BUDGET:g}-second time budget for one call ran out")) + "\n")
        sys.stderr.flush()
    finally:
        os._exit(2)


def _start_watchdog() -> threading.Timer:
    timer = threading.Timer(GATE_TIME_BUDGET + WATCHDOG_GRACE, _watchdog_fire)
    timer.daemon = True
    timer.start()
    return timer


def _decide(tool_name: str, tool_input: dict, workspace: Path, cwd: Path | None,
            org_rules: bool = True, guarded: list[Path] | None = None) -> tuple[bool, str]:
    """The build-only checks, plus (here, so every caller of this function gets it:
    build-only's decide() and connected mode's decide_connected(), not only full
    mode's own separate call) the same protected-record check full mode applies on
    its own: an engagement's binding, and a client's consent, approval and control
    records, are kept from every recognized tool regardless of whether their folder
    is itself guarded below. Here that check is strict: an error inside it blocks
    the call (fail closed), where full mode's own call lets the call through. With
    org_rules False (connected mode), org calls and
    Torque commands pass the rest of this scan (connected mode classifies them
    itself), and `guarded` lists the folders treated as client context (other
    clients' folders, and internal initiatives) in place of the whole clients/
    folder."""
    workspace = Path(os.path.realpath(str(workspace)))
    cwd = Path(os.path.realpath(str(cwd))) if cwd is not None else workspace
    reason = _approval_file_reason(tool_name, tool_input, cwd, strict=True)
    if reason:
        return False, reason
    allowed, reason = _decide_root(tool_name, tool_input, workspace, cwd, False, org_rules, guarded)
    if not allowed:
        return allowed, reason
    try:
        copies_here = _worktree_copies(workspace)
    except OSError as exc:
        folder = workspace.joinpath(*WORKTREES_DIR)
        return False, (f"Build-only mode: the worktrees folder {folder} could not be listed "
                       f"({type(exc).__name__}), so a copy of clients/ it may hold cannot be guarded; blocking "
                       "to fail closed. Fix its permissions or remove it, outside the AI session.")
    for copy in copies_here:
        copies = None if guarded is None else [
            copy / Path(os.path.realpath(str(folder))).relative_to(workspace)
            if Path(os.path.realpath(str(folder))).is_relative_to(workspace)
            else copy / "clients" / Path(folder).name
            for folder in guarded]
        allowed, reason = _decide_root(tool_name, tool_input, copy, cwd, True, org_rules, copies)
        if not allowed:
            return allowed, reason
    return True, ""


def _decide_root(tool_name: str, tool_input: dict, workspace: Path, cwd: Path, copy: bool,
                 org_rules: bool = True, guarded: list[Path] | None = None) -> tuple[bool, str]:
    """_decide_root_for each guarded folder (by default, workspace/clients). An
    empty list still runs once, against a folder that does not exist, so the
    integrity checks (settings, gate files, interpreter, unknown tools) apply."""
    folders = guarded if guarded is not None else [workspace / "clients"]
    for folder in folders or [workspace / "clients" / ".none"]:
        clients = Path(os.path.realpath(str(folder)))
        allowed, reason = _decide_root_for(tool_name, tool_input, workspace, cwd, copy, clients, org_rules)
        if not allowed:
            return allowed, reason
    return True, ""


def _decide_root_for(tool_name: str, tool_input: dict, workspace: Path, cwd: Path, copy: bool, clients: Path,
                     org_rules: bool = True) -> tuple[bool, str]:
    """decide() for one root: the workspace, or (copy=True) a worktree copy of it
    under .claude/worktrees/, whose clients/ is guarded the same way."""
    claude_dir = Path(os.path.realpath(str(workspace / ".claude")))
    if tool_name.startswith("mcp__") or tool_name in MCP_LIKE_TOOLS:
        # An MCP tool that runs a command (a shell or process server) gets the
        # Bash scan on its command strings before its path arguments are checked.
        for text in _command_strings(tool_input):
            reason = _scan_bash(text, clients, claude_dir, workspace, cwd, org_rules=org_rules)
            if reason:
                return False, _command_block_message(reason)
    if org_rules and (tool_name.startswith("mcp__") or tool_name in MCP_LIKE_TOOLS):
        # Connected mode (org_rules False) classifies these calls itself.
        reason = _mcp_org_reason(tool_name, tool_input)
        if reason:
            return False, reason
    if tool_name.startswith("mcp__"):
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
        # the Bash tool's shell; PowerShell gets the same best-effort scan on its
        # command as the Bash scan can read it, and on the strings it can run.
        texts = [str(command or "")]
        if "powershell" in tool_name.casefold():
            try:
                texts = _powershell_commands(texts[0])
            except ValueError as exc:
                return False, _command_block_message(str(exc))
        for text in texts:
            reason = _scan_bash(text, clients, claude_dir, workspace, cwd, org_rules=org_rules)
            if reason:
                return False, _command_block_message(reason)
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
        if tool_name == "Glob":
            # A brace group ({clients,x}, {x,cl{i,j}ents}) is a set of alternatives:
            # read each as a whole pathname (spaces kept), so an alternative rooted at
            # or above clients/ is not hidden behind a literal-looking {..} prefix. A
            # brace that does not resolve is read as a wildcard (_glob_alternatives).
            alternatives = _glob_alternatives(pattern)
            if any(_reaches(_glob_pattern_root(root, alt), clients) for alt in alternatives):
                return False, ("Build-only mode: client context stays out of the AI session. This Glob pattern "
                               "climbs (..) or is rooted at or above clients/." + NARROW_PATH_HINT)
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
            return False, ("Build-only mode: only the owner changes workspace.json, the hook configuration, or a "
                           "client's consent and approval records.")
        if write and not org_rules and _is_within(target, claude_dir) \
                and not _is_within(target, claude_dir / "worktrees"):
            # R51: the same protection _token_targets_claude_dir already gives a Bash
            # write to anything under .claude/ (the hook configuration directory,
            # including the connected-mode permission profile sidecar), for the file
            # tools (Write, Edit, MultiEdit, NotebookEdit) that reach this branch.
            # Connected-mode only (org_rules False): common.md's "build-only and full
            # behavior do not change" -- build-only keeps exactly a15's file-tool
            # behavior for .claude/. A worktree copy under .claude/worktrees/ is
            # exempt: each copy gets its own _decide_root pass (copy=True), which
            # already guards that copy's own contents on its own terms.
            return False, "Build-only mode: only the owner changes the .claude hook configuration directory."
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


# The most alternatives a Glob pattern's braces are expanded into; past it the pattern
# is read conservatively, as when a brace does not close.
_GLOB_ALTERNATIVE_LIMIT = 256


def _brace_alternatives(word: str, budget: list[int]) -> list[str] | None:
    """The words one pathname's brace groups make, nested groups included
    ({a,{b,c}}d is ad bd cd), each kept whole. A group with no comma ({x}) is
    literal. None when a brace does not close or the words pass the limit."""
    start = word.find("{")
    while start >= 0:
        depth, commas, end = 0, [], -1
        for at in range(start, len(word)):
            ch = word[at]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if not depth:
                    end = at
                    break
            elif ch == "," and depth == 1:
                commas.append(at)
        if end < 0:
            return None
        if commas:
            out: list[str] = []
            for a, b in zip([start, *commas], [*commas, end]):
                made = _brace_alternatives(word[:start] + word[a + 1:b] + word[end + 1:], budget)
                if made is None:
                    return None
                out += made
                budget[0] -= len(made)
                if budget[0] < 0:
                    return None
            return out
        start = word.find("{", end + 1)
    return [word]


def _glob_alternatives(pattern: str) -> list[str]:
    """The pathnames a Glob pattern stands for: its brace alternatives, each a whole
    pathname (a space in a folder name is part of it). When the braces do not resolve
    (one never closes, or there are too many alternatives), the pattern is read with
    its first brace as a wildcard, so its root is the folder before that brace."""
    made = _brace_alternatives(pattern, [_GLOB_ALTERNATIVE_LIMIT])
    if made is not None:
        return made
    return [pattern.replace("{", "*", 1)]


def _glob_pattern_root(root: Path, pattern: str) -> Path:
    """The folder a Glob pattern can reach from root: its leading components
    without a wildcard (an absolute pattern starts from its own root), then one
    level up for each `..` later in the pattern that climbs past what came before
    it. `**` may match nothing, so it adds no depth."""
    parts = re.split(r"[/\\]", pattern)
    fixed = 0
    for part in parts:
        if _has_glob_char(part):
            break
        fixed += 1
    try:
        base = _resolve(root, "/".join(parts[:fixed]) or ("/" if pattern[:1] in "/\\" and pattern else "."))
    except (OSError, ValueError):
        return root
    depth = lowest = 0
    for part in parts[fixed:]:
        if part == "..":
            depth -= 1
        elif part not in ("", ".", "**"):
            depth += 1
        lowest = min(lowest, depth)
    for _ in range(-lowest):
        base = base.parent
    return base


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


MODE_STRICTNESS = {"build-only": 2, "connected": 1, "full": 0}


# How connected mode is written in workspace.json while guarded reads are on. A Torque
# from before guarded reads does not know the word, and anything it does not know
# is build-only to it (below): its hook then refuses org work in that workspace
# instead of applying its own, older reading of connected mode.
CONNECTED_GUARDED = "connected-guarded"


def _resolve_ai_access(value: object, approval: object = None) -> str:
    """Only an explicit "full" means full, and only "connected" together with
    approval "required" means connected. Everything else present (null, an
    empty string, a typo, wrong case, a non-string, connected without required
    approval) fails closed to build-only. A missing key is handled by the caller
    and means full, the documented default."""
    if value == "full":
        return "full"
    if value in ("connected", CONNECTED_GUARDED) and approval == "required":
        return "connected"
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
            mode = (_resolve_ai_access(data["ai_access"], data.get("approval"))
                    if "ai_access" in data else "full")
            chain.append((folder, mode, True))
        elif _is_workspace_marker(folder):
            chain.append((folder, "build-only", False))
    return chain


def _workspace_mode(start: Path) -> tuple[Path, str, bool]:
    """The governing workspace for start: the strictest wins (build-only, then
    connected, then full). A nested workspace.json (even `{}` or an explicit
    "full") cannot loosen a stricter workspace above it. Returns the nearest
    workspace of the strictest mode, else (start, "full", True)."""
    chain = _workspace_chain(start)
    if not chain:
        return (start, "full", True)
    strictest = max(MODE_STRICTNESS[entry[1]] for entry in chain)
    return next(entry for entry in chain if MODE_STRICTNESS[entry[1]] == strictest)


def _mode_problem(folder: Path) -> str:
    """One sentence when `folder` is build-only because its configuration could not be
    read or was not understood, rather than because the owner set build-only; else "".
    It names the file, what was found and the owner's fix, so a consultant hit by
    blocks for a mode nobody chose has a pointer (claude-A2a#8). An absent ai_access
    key means full by design, so it is not a problem; a deliberate "build-only" is
    not either."""
    config = folder / "workspace.json"
    fix = f"run torque workspace ai-access full --path {folder} to set the mode"
    if config.is_file():
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("workspace.json must be a JSON object")
        except (OSError, ValueError) as exc:
            return (f"workspace.json in {folder} could not be read ({type(exc).__name__}: {exc}), so this "
                    f"workspace is build-only. Correct the JSON, or {fix}.")
        if "ai_access" not in data:
            return ""
        value = data["ai_access"]
        if value == "build-only" or _resolve_ai_access(value, data.get("approval")) != "build-only":
            return ""
        if value in ("connected", CONNECTED_GUARDED):
            return (f"workspace.json in {folder} sets ai_access to {value!r} without \"approval\": "
                    f"\"required\", so this workspace falls back to build-only. Add the approval setting, or {fix}.")
        return (f"workspace.json in {folder} sets ai_access to {value!r}, which is not a known mode, so this "
                f"workspace is build-only. Use \"full\", \"build-only\" or \"connected\", or {fix}.")
    if _is_workspace_marker(folder):
        return (f"{folder} has a workspace marker (clients/ and .torque/templates.json) but no readable "
                f"workspace.json, so it is treated as build-only. {fix[0].upper() + fix[1:]}.")
    return ""


# The command a Claude Code hook should run. If `torque.gate` cannot be imported
# by the hook's interpreter (Torque missing, a broken install, an edited gate),
# the excepthook exits 2 (block) instead of Python's default exit 1, which
# Claude Code treats as a non-blocking error. It contains no double quote and no
# percent sign, so it can be wrapped in double quotes for sh, bash and cmd.
# hook_command runs it with -I (isolated mode, Python 3.4+): the working
# directory, PYTHONPATH and the user site directory stay off sys.path, so a
# torque/ folder or sitecustomize.py planted in the workspace is never imported.
# A host that reports a blocked call quotes this whole command before the gate's reason, so
# the text inside it names no mode and reads as the fallback it is. Its message says how to
# recover outside the blocked session. It works as one argv element too (an exec-form hook
# entry: command = the interpreter, args = ["-I", "-c", HOOK_SHIM_CODE]); doctor recognizes
# the shim by its sys.excepthook, so a hook written with an earlier message still counts.
HOOK_SHIM_CODE = ("import os,sys;sys.excepthook=lambda t,e,b:(print('(Torque gate "
                  "could not load: '+t.__name__+': '+str(e)+'; call blocked. In your own terminal, run "
                  "torque doctor --workspace W, with W the workspace folder, to see why.)',"
                  "file=sys.stderr,flush=True),os._exit(2));from torque.gate import main;sys.exit(main())")


def hook_command(python: str) -> str:
    """The hook command for a given interpreter path (use forward slashes on Windows)."""
    return f'"{python}" -I -c "{HOOK_SHIM_CODE}"'


# Argument names that hold a tool's file CONTENT, not a path it acts on: a file
# tool's new text, a MultiEdit's edits, a Bash command's human description. They are
# never a path, so workspace discovery skips them rather than resolve each word of a
# large body as a path (the 0.3-1.4 s cost claude-A2a#4 measured).
_CONTENT_KEYS = ("content", "old_string", "new_string", "new_source", "edits", "description")


def _path_strings(value: object, depth: int = 0):
    """Every string a tool_input holds that could name a path, nested: like
    _string_values, but skipping the values under a content key (_CONTENT_KEYS),
    which are text the tool writes, never a path it acts on."""
    if depth > 8:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and key.casefold() in _CONTENT_KEYS:
                continue
            yield from _path_strings(item, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _path_strings(item, depth + 1)


def _touched_paths(tool_input: dict, cwd: Path) -> list[Path]:
    """Paths a tool call names, resolved from cwd: every string argument that is not
    a content field as a path, and every word of a string that looks like a command.
    Words are collected and deduplicated before any is resolved, so a path that
    repeats across arguments (or across a command's words) is resolved once."""
    words: list[str] = []
    for raw in itertools.islice(_path_strings(tool_input), 64):
        if not raw or "\x00" in raw or len(raw) > MAX_INPUT_CHARS:
            continue
        words.append(_uri_path(raw))
        if any(c in raw for c in " \t\n;&|<>"):
            try:
                expanded = _expand_braces(_decode_ansi_c(_expand_home_in_command(raw)))
            except ValueError:
                expanded = raw
            words += [_token_operand(t) for toks, _ in _segments_with_separators(expanded) for t in toks]
    out: list[Path] = []
    for word in dict.fromkeys(words):
        if not word or "\n" in word or len(word) > 4096:
            continue
        try:
            out.append(_resolve(cwd, word))
        except (OSError, ValueError):
            continue
    return list(dict.fromkeys(out))


# The longest file name component most filesystems accept, in bytes.
_NAME_MAX = 255


def _path_max(start: Path) -> int:
    try:
        return int(os.pathconf(start.anchor or "/", "PC_PATH_MAX"))
    except (AttributeError, OSError, ValueError):
        return 1024


def _not_a_path(start: Path, exc: OSError) -> bool:
    """True when looking up start failed only because it is not a path at all:
    the name is too long (ENAMETOOLONG, or Windows' ERROR_FILENAME_EXCED_RANGE)
    and the kernel would refuse it for any tool, because a component is longer
    than any file name or the whole is longer than the longest path, as when a
    whole command or a long commit message is read as a path. Any other error,
    or a too-long path the kernel would accept, still fails closed."""
    too_long = exc.errno == errno.ENAMETOOLONG or getattr(exc, "winerror", None) == 206
    if not too_long:
        return False
    encoded = [part.encode("utf-8", "surrogateescape") for part in start.parts]
    return (any(len(part) > _NAME_MAX for part in encoded)
            or len(str(start).encode("utf-8", "surrogateescape")) >= _path_max(start))


def _discovery_starts(cwd: Path, touched: list[Path]) -> list[Path]:
    """Where to look for a governing workspace: the event's cwd, the session's
    project directory (CLAUDE_PROJECT_DIR, which Claude Code sets for hooks, so
    leaving the workspace with `cd ..` does not end the session's gating), and every
    path the call names (`touched`, computed once per call and shared)."""
    starts = [cwd]
    project = os.environ.get("CLAUDE_PROJECT_DIR")
    if project:
        starts.append(Path(os.path.realpath(project)))
    return starts + touched


def _gated_workspaces(cwd: Path, touched: list[Path]) -> list[Path]:
    """Every build-only workspace at or above a discovery start (_discovery_starts)."""
    gated: list[Path] = []
    checked: set[str] = set()
    for start in _discovery_starts(cwd, touched):
        key = _cf(str(start))
        if key in checked:
            continue
        checked.add(key)
        try:
            chain = _workspace_chain(start)
        except OSError as exc:
            if _not_a_path(start, exc):
                continue
            raise
        for folder, mode, _ in chain:
            if mode == "build-only" and folder not in gated:
                gated.append(folder)
    return gated


_PROTECTED_NAMES = ("consent.json", "consent-evidence", "approvals", "binding.json", "control", "requests", "claims")
_ENGAGEMENT_RECORDS = {"clients": _PROTECTED_NAMES, "initiatives": ("binding.json",)}
_RECORD_NAMES = frozenset(_cf(name) for names in _ENGAGEMENT_RECORDS.values() for name in names)
_REMOVERS = {"rm", "rmdir", "mv", "unlink", "shred", "truncate", "rsync", "find", "chmod", "chown", "ln", "cp",
             "ditto", "tar", "unzip", "git"}


def _is_workspace_root(folder: Path) -> bool:
    return (folder / "workspace.json").is_file() or _is_workspace_marker(folder)


def _protected_record(path: Path) -> bool:
    """path is an engagement's binding, or a client's consent, consent evidence,
    approval, control, request or claim record (or inside one), in a Torque
    workspace. Folder and record names are matched case-insensitively (as _cf
    matches everywhere else), since a case variant reaches the same file or
    folder as the canonical name on a case-insensitive filesystem."""
    parts = path.parts
    for index in range(len(parts) - 3, -1, -1):
        names = _ENGAGEMENT_RECORDS.get(_cf(parts[index]))
        if names and any(_cf(parts[index + 2]) == _cf(name) for name in names):
            return _is_workspace_root(Path(*parts[:index]))
    return False


def _holds_records(path: Path) -> bool:
    """path is a folder that holds such records: an engagement folder, clients/ or
    initiatives/, or the workspace root. Matched case-insensitively, as
    _protected_record is. The os.path checks treat an unreachable name (one too
    long, say) as absent on every Python version, as pathlib does from 3.14."""
    try:
        if not os.path.isdir(path):
            return False
        names = _ENGAGEMENT_RECORDS.get(_cf(path.parent.name))
        if names and _is_workspace_root(path.parent.parent):
            return any(os.path.exists(path / name) for name in names)
        for folder, names in _ENGAGEMENT_RECORDS.items():
            base = path if _cf(path.name) == _cf(folder) else path / folder
            if os.path.isdir(base) and _is_workspace_root(base.parent) and any(
                    os.path.exists(child / name) for child in base.iterdir() if os.path.isdir(child) for name in names):
                return True
    except OSError:
        return True
    return False


def _approval_file_reason(tool_name: str, tool_input: dict, cwd: Path, strict: bool = False) -> str:
    """In every mode (full, build-only and connected), an engagement's binding and
    a client's consent, consent evidence, approval, control, request and claim
    records, and the approval key, are kept from recognized tools, with every path
    resolved first (relative paths and folders that hold them included): a record
    changed by a tool a mode's own scan does not separately cover (or while a
    stricter mode is off) would be trusted once the owner relies on it.

    In full mode the check is best effort: it never blocks by running long or by
    failing in a way that would also fail a retry. When it cannot finish (an error
    inside it, or its time or glob budget spent), full mode falls back to a cheap
    text test that cannot itself fail (_text_names_record): the call is blocked
    only when its own text spells a protected record, a folder that holds them or
    the approval key, and allowed otherwise, as before. With `strict` (build-only
    and connected mode, through _decide) it fails closed: an error blocks the call
    with a reason, and a spent budget is raised for the caller to block on."""
    try:
        return _approval_file_targets_reason(tool_name, tool_input, cwd, strict)
    except BudgetExceeded:
        if strict:
            raise
        return _RECORD_REASON if _text_names_record(tool_input) else ""
    except Exception as exc:
        if strict:
            return ("Torque: the check that keeps tools away from consent, approval and control records could "
                    f"not read this call ({type(exc).__name__}: {exc}), so the call is blocked to fail closed. "
                    "Write the command more simply, or run it yourself outside the AI session.")
        return _RECORD_REASON if _text_names_record(tool_input) else ""


_RECORD_REASON = ("Torque: a client's consent, approval and control records, an engagement's binding and the "
                  "approval key are changed only by the consultant's torque commands, in every mode.")
# A folder that holds engagement records, named anywhere in the text as a whole
# path component: clients or initiatives, as a bare word (`rm -rf clients`), after
# a separator (`../clients/acme`) or before one (`clients/acme`). The surrounding
# character classes exclude a word character or a dot, so `myclients`, `clientsx`
# and `x.clients` do not match. On the error path a prose mention also matches,
# which blocks there rather than fails open (the safe direction).
_RECORD_FOLDER_RE = re.compile(r"(?<![\w.])(clients|initiatives)(?![\w.])", re.IGNORECASE)


def _text_names_record(tool_input: dict) -> bool:
    """A pure-text net for full mode's error path, with no disk access and no
    pattern that can run long: True when a string the call carries spells a
    protected record (APPROVAL_FILE_RE), the approval key (APPROVAL_KEY_RE),
    workspace.json, or a clients/ or initiatives/ path. It cannot resolve a
    relative path or follow a cd, so it is a floor under fail-open, never the
    strict check. A read that merely mentions such a folder (no record) is left
    to the resolving check; this net errs toward blocking only the error path."""
    for raw in itertools.islice(_string_values(tool_input), 256):
        if not isinstance(raw, str) or len(raw) > MAX_INPUT_CHARS:
            continue
        if (_targets_guarded_file(raw) or _RECORD_FOLDER_RE.search(raw)
                or APPROVAL_KEY_RE.search(raw)):
            return True
    return False


_CACHED_GIT_RM_OPTIONS = frozenset({"--cached", "-r", "-q", "--quiet", "-f", "--force",
                                    "--ignore-unmatch", "-n", "--dry-run"})
_SHELL_EXPANSION_CHARS = frozenset("$`*?[]{}")


def _is_cached_git_rm(toks: list[str]) -> bool:
    """`git rm --cached <path>...` only removes git's index entries, never a file in
    the working tree, so it is not a removal or write against the protected-record
    check. git's option parsing can turn a "cached"-looking command into a real
    delete (`--end-of-options` or a bare `--` make a later `--cached` a path,
    `--no-cached` negates it, `--no-cache` abbreviates the negation), so this is a
    strict allowlist: `git` then `rm` with no global options between, no output
    redirection, every option word exactly one of _CACHED_GIT_RM_OPTIONS (combined
    short flags such as -rf are refused), `--cached` among them, and at least one
    path. No word after `rm` may hold `$`, a backtick or any of `*?[]{}`: bash
    would expand it (`$X`, `$'--no-cached'`, `${X:-...}`, a glob, braces) after the
    gate decides, possibly into an option such as --no-cached. shlex keeps `$`
    (it only strips the quotes around it), so a quoted `$` still counts here.
    Anything else is judged as an ordinary remover."""
    words = _without_redirections(toks)
    if toks != words or len(words) < 3 or _basename(words[0]) != "git" or words[1] != "rm":
        return False
    if any(ch in _SHELL_EXPANSION_CHARS for w in words[2:] for ch in w):
        return False
    options = [w for w in words[2:] if w.startswith("-")]
    paths = [w for w in words[2:] if not w.startswith("-")]
    return (all(w in _CACHED_GIT_RM_OPTIONS for w in options)
            and "--cached" in options and bool(paths))


def _approval_file_targets_reason(tool_name: str, tool_input: dict, cwd: Path, strict: bool = False) -> str:
    reason = ("Torque: a client's consent, approval and control records, an engagement's binding and the "
              "approval key are changed only by the consultant's torque commands, in every mode.")
    key = PATH_TOOLS.get(tool_name)
    if key:
        raw = tool_input.get(key)
        if tool_name not in READ_TOOLS and isinstance(raw, str) and raw:
            target = _resolve(cwd, raw)
            if APPROVAL_KEY_RE.search(target.as_posix()) or _protected_record(target):
                return reason
        return ""
    for text in _command_strings(tool_input):
        if len(text) > MAX_INPUT_CHARS:
            continue
        readings = [text]
        if "powershell" in tool_name.casefold():
            # Read as the build-only scan reads a PowerShell call (backslashes as
            # separators, its cmdlets under the names the scan knows, the strings it
            # runs). A call that cannot be read that way is refused by the build-only
            # scan; here its plain reading is checked.
            try:
                readings = _powershell_commands(text)
            except ValueError:
                readings = [_powershell_as_bash(text)]
        if any(_command_targets_records(reading, cwd, strict) for reading in readings):
            return reason
    return ""


# Interpreters that run a string of code given on the command line: python/py -c,
# node -e/-p/--eval/--print, perl -e/-E, ruby -e, php -r. Matched by basename, so a
# full path (/usr/bin/python3) and a versioned name (python3.11, python3.11.exe) count.
# The gate reads that code for the literal paths it names and refuses it when one is a
# protected record (_inline_code_names_record); `python -m torque` and record-free code
# pass. Paths the code builds at run time are not seen (the documented limit).
# Per interpreter: letters that carry the code, letters that take a value (the rest of
# the word or the next one), letters that take the rest of the word, letters that stop
# option reading (python -m, php -f).
_INLINE_OPTIONS = {"python": ("c", "WX", "", "m"), "perl": ("eE", "I", "iMmFCdDxV", ""),
                   "ruby": ("e", "rIC", "EFTK0ix", ""), "php": ("r", "dcz", "", "f")}
_NODE_VALUE_OPTIONS = ("-r", "--require", "--import", "--loader", "-C", "--conditions")
# Runners: a command word that executes its next non-option word. Per runner, the
# options that take a separate value and the number of operands before the command.
# `then`, `do`, `else`, `!` and `{` just lead into a command.
_RUNNERS = {
    "env": ({"-u", "-C", "-S", "--unset", "--chdir", "--split-string"}, 0),
    "sudo": ({"-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T", "-U", "-r", "-t", "--user", "--group", "--host",
              "--prompt", "--chdir", "--role", "--type"}, 0),
    "doas": ({"-u", "-C"}, 0), "nice": ({"-n", "--adjustment"}, 0), "ionice": ({"-c", "-n", "-p", "-t"}, 0),
    "nohup": (set(), 0), "time": ({"-f", "-o", "--format", "--output"}, 0), "command": (set(), 0),
    "exec": ({"-a"}, 0), "builtin": (set(), 0), "setsid": (set(), 0), "unbuffer": (set(), 0),
    "stdbuf": ({"-i", "-o", "-e"}, 0), "timeout": ({"-s", "-k", "--signal", "--kill-after"}, 1),
    "flock": ({"-w", "-E", "--timeout", "--conflict-exit-code"}, 1), "xargs": (
        {"-I", "-L", "-n", "-P", "-s", "-d", "-E", "-a", "--max-args", "--max-procs", "--delimiter"}, 0),
    "winpty": (set(), 0), "wsl": ({"-d", "-u", "--distribution", "--user"}, 0), "arch": (set(), 0),
    "caffeinate": ({"-t", "-w"}, 0), "cmd": (set(), 0), "start": (set(), 0), "call": (set(), 0)}
_PACKAGE_RUNNERS = {"uv": ("run", "tool", "x"), "poetry": ("run",), "pipenv": ("run",), "pipx": ("run",),
                    "pnpm": ("exec", "dlx"), "yarn": ("exec", "run"), "conda": ("run",), "rye": ("run",),
                    "hatch": ("run",), "npx": ()}
_LEADERS = frozenset({"then", "do", "else", "elif", "if", "while", "until", "!", "{"})
_FIND_EXEC = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
_REDIRECT_WORD_RE = re.compile(r"^(\d*|&)(>>?|<<<?|>&|<&)(.*)$", re.DOTALL)
_CODE_LITERAL_RE = re.compile(r"'((?:[^'\\\n]|\\.)*)'|\"((?:[^\"\\\n]|\\.)*)\"|`((?:[^`\\\n]|\\.)*)`")
# An actual call that removes or moves a path, matched on code with its string literals
# blanked, so a printed word such as 'rename' is not one.
_CODE_REMOVER_RE = re.compile(
    r"\b(shutil\.(rmtree|move)|os\.(remove|unlink|rmdir|removedirs|rename|renames|replace)|rmtree|rimraf"
    r"|unlinkSync|rmSync|rmdirSync|renameSync|fs\.(rm|rmdir|unlink|rename)|File\.(delete|unlink|rename)"
    r"|FileUtils\.(rm|rm_r|rm_rf|rmdir|remove_dir|remove_entry|mv))\s*\("
    r"|\.(unlink|rmdir|rename)\s*\(|\b(unlink|rmdir|rename)\b\s*[\"(]")


def _shell_words(text: str) -> list:
    """The simple commands in text, in order, split at the separators outside quotes
    (; newline). Each is a list of words, a word being (shell reading, reading that
    keeps a backslash, so a Windows path stays one); the interpreter check accepts
    either. A parenthesis or backtick, and the separators that make the next command
    conditional or parallel (&& || | &), are marker strings between commands. A quote that never closes runs to the end."""
    out: list = [[]]
    sh = lit = ""
    started = False
    i, n = 0, len(text)

    def flush() -> None:
        nonlocal sh, lit, started
        if started:
            out[-1].append((sh, lit))
        sh = lit = ""
        started = False
    while i < n:
        ch = text[i]
        if ch in " \t\r":
            flush()
        elif ch in ";\n":
            flush()
            out.append([])
        elif ch in "&|" and not (ch == "&" and (text[i - 1:i] in ("<", ">") or text[i + 1:i + 2] == ">")):
            flush()
            op = ch * 2 if text[i + 1:i + 2] == ch else ch
            i += len(op) - 1
            out.extend([op, []])
        elif ch in "()`":
            flush()
            out.extend([ch, []])
        elif ch == "'":
            end = text.find("'", i + 1)
            end = n if end < 0 else end
            sh += text[i + 1:end]
            lit += text[i + 1:end]
            started = True
            i = end
        elif ch == '"':
            started = True
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n and text[i + 1] in '"\\$`':
                    sh += text[i + 1]
                    lit += text[i:i + 2]
                    i += 2
                    continue
                sh += text[i]
                lit += text[i]
                i += 1
        elif ch == "\\" and i + 1 < n:
            started = True
            lit += ch
            if text[i + 1] != "\n":
                sh += text[i + 1]
                lit += text[i + 1]
            i += 1
        else:
            sh += ch
            lit += ch
            started = True
        i += 1
    flush()
    return [entry for entry in out if entry]


def _skip_options(words: list[tuple[str, str]], i: int, takes: set[str],
                  chdir: frozenset[str] = frozenset()) -> tuple[int, bool]:
    """Index after a runner's options (a flag, or a flag and its separate value), and
    whether one of them changes the directory the command runs in (env -C, --chdir)."""
    moves = False
    while i < len(words) and words[i][0].startswith("-") and words[i][0] != "-":
        flag = words[i][0]
        i += 1
        name = flag.split("=", 1)[0]
        moves = moves or name in chdir or any(flag.startswith(c) and len(c) == 2 and len(flag) > 2 for c in chdir)
        if flag == "--":
            break
        if flag in takes:
            i += 1
    return i, moves


_RUNNER_CHDIR = {"env": frozenset({"-C", "--chdir"}), "sudo": frozenset({"-D", "--chdir"}),
                 "wsl": frozenset({"--cd"})}


def _expand_env_split(words: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """`env -S STRING` (also -SSTRING, --split-string STRING and =STRING) runs the
    words of STRING as the command: put them where the option stood."""
    for _ in range(4):
        for k, word in enumerate(words):
            if _basename(word[0]) != "env":
                continue
            j = k + 1
            while j < len(words) and words[j][0].startswith("-") and words[j][0] != "-":
                flag = words[j][0]
                name = flag.split("=", 1)[0]
                if name == "--split-string" or flag == "-S" or (flag.startswith("-S") and not flag.startswith("--")):
                    if "=" in flag and flag.startswith("--"):
                        text, used = flag.split("=", 1)[1], 1
                    elif flag.startswith("-S") and len(flag) > 2:
                        text, used = flag[2:], 1
                    else:
                        text, used = (words[j + 1][0] if j + 1 < len(words) else ""), 2
                    inner = [w for cmd in _shell_words(text) if isinstance(cmd, list) for w in cmd]
                    words = words[:j] + inner + words[j + used:]
                    break
                j += 2 if flag in ("-u", "-C", "--unset", "--chdir") else 1
            else:
                continue
            break
        else:
            return words
    return words


def _exec_index(words: list[tuple[str, str]], at: int = 0) -> tuple[int | None, bool]:
    """Where the command a simple command really runs starts, and whether a runner on
    the way changes its directory (env -C, sudo -D, --chdir, find -execdir). The index
    is after VAR=value assignments, redirections with their targets (`> out python ...`)
    and the options of each runner that executes its next non-option word (env, sudo,
    nice, nohup, time, command, exec, xargs, find -exec, ...). The arguments of any
    other command are never an executable (`echo python -c x`, `env echo python -c x`)."""
    moves = False
    while at < len(words):
        word = words[at][0]
        name = _basename(word)
        match = _REDIRECT_WORD_RE.match(word)
        if match:
            at += 1 if match.group(3) else 2          # `>file`, or `>` and its target
        elif re.match(r"^[A-Za-z_]\w*=", word):
            at += 1
        elif name in _LEADERS:
            at += 1
        elif name in _RUNNERS:
            takes, operands = _RUNNERS[name]
            first = at + 1
            at, here = _skip_options(words, at + 1, takes, _RUNNER_CHDIR.get(name, frozenset()))
            moves = moves or here
            if name == "command" and any(re.fullmatch(r"-[pvV]*[vV][pvV]*", w[0]) for w in words[first:at]):
                return None, moves            # command -v/-V looks a name up and runs nothing
            while at < len(words) and re.fullmatch(r"/\w", words[at][0]):
                at += 1                                # cmd /c, start /b
            if name == "env":
                while at < len(words) and re.match(r"^[A-Za-z_]\w*=", words[at][0]):
                    at += 1
            at += operands
        elif name in _PACKAGE_RUNNERS:
            at, _ = _skip_options(words, at + 1, set())
            subs = _PACKAGE_RUNNERS[name]
            if subs:
                if at >= len(words) or words[at][0] not in subs:
                    return None, moves
                at, _ = _skip_options(words, at + 1, set())
        elif name == "find":
            hits = [j for j in range(at + 1, len(words)) if words[j][0] in _FIND_EXEC]
            moves = moves or any(words[j][0] in ("-execdir", "-okdir") for j in hits[:1])
            at = hits[0] + 1 if hits else len(words)
        else:
            return at, moves
    return None, moves


def _drop_redirections(words: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The words with each redirection (and its separate target) taken out."""
    kept, i = [], 0
    while i < len(words):
        match = _REDIRECT_WORD_RE.match(words[i][0])
        if match:
            i += 1 if match.group(3) else 2
        else:
            kept.append(words[i])
            i += 1
    return kept


def _inline_flag_code(family: str, args: list[str]) -> list[str]:
    """The code strings an interpreter is given, read from its arguments as it reads
    its options: they end at the script or module name, short flags may share a word
    (-Ic, -ne), and a code flag takes the rest of its word or the next word. Python
    runs only the first -c: what follows is its sys.argv. Node, perl, ruby and php may
    be given code more than once."""
    out: list[str] = []
    i = 0

    def value(rest: str) -> str:
        nonlocal i
        if rest or i >= len(args):
            return rest
        i += 1
        return args[i - 1]
    while i < len(args):
        word = args[i]
        i += 1
        if word == "--" or not word.startswith("-") or word == "-":
            break
        if family == "node":
            name, eq, rest = word.partition("=")
            if name in ("--eval", "--print"):
                out.append(value(rest))
            elif name in _NODE_VALUE_OPTIONS:
                value(rest)
            elif re.fullmatch(r"-[ep]+", word):
                out.append(value(""))
            continue
        if family == "python" and re.fullmatch(r"-(\d(\.\d+)?(-\d+)?|V:.*)", word):
            continue                  # the Windows launcher's version selector
        if word.startswith("--"):
            continue
        code, takes, takes_rest, stops = _INLINE_OPTIONS[family]
        for at, letter in enumerate(word[1:], 2):
            rest = word[at:]
            if letter in stops:
                return out
            if letter in code:
                out.append(value(rest))
                if family == "python":
                    return out
                break
            if letter in takes:
                value(rest)
                break
            if letter in takes_rest:
                break
    return out


def _interpreter_family(name: str) -> str:
    if PY_LAUNCHER_RE.match(name):
        return "python"
    if re.fullmatch(r"node(js)?", name):
        return "node"
    return name if name in _INLINE_OPTIONS else ""


def _inline_codes(words: list[tuple[str, str]], at: int) -> list[str]:
    """The inline-code strings the command that starts at `at` runs, when it is an
    interpreter. A redirection among its words (`python > out -c code`) is not one."""
    family = next((f for f in (_interpreter_family(_basename(shown)) for shown in words[at]) if f), "")
    args = _drop_redirections(words[at + 1:])
    return _inline_flag_code(family, [word[0] for word in args]) if family else []


_LOOSE_CD_CHARS = frozenset("$`*?[{~")
_CODE_CHDIR_RE = re.compile(r"\b(f?chdir)\b")
_CONDITIONAL_SEPARATORS = frozenset({"&&", "||", "|", "&"})


def _inline_code_calls(text: str, start: list[Path]) -> list[tuple[str, list[Path] | None]]:
    """Every inline-code string the commands in text run, with the folders each runs
    from, or None when the folder is not certain. One reading of the text gives the code
    and its folder together. A `cd` (or pushd/popd) that is a command of its own, run
    unconditionally, changes the folder for the commands after it, and a parenthesis or
    backtick group gives it back. The folder is uncertain after a cd that is conditional
    (after `&&`, `||`, or before `|`, `&`, `||`), in a loop or block, with a target the
    text does not spell out, and for a command run through a wrapper that changes its
    folder (env -C, find -execdir), for code that changes its own (os.chdir), and after a
    cd whose target is not a folder now (the cd fails and the shell stays put). The caller
    treats an uncertain folder strictly for code that names a record file."""
    state: list[Path] | None = list(start)
    scopes: list[list[Path] | None] = []
    dirs: list[list[Path] | None] = []
    found: list[tuple[str, list[Path] | None]] = []
    ticks = False
    entries = _shell_words(text)
    before = None
    for k, entry in enumerate(entries):
        if isinstance(entry, str):
            if entry in _CONDITIONAL_SEPARATORS:
                before = entry
            elif entry == "(" or (entry == "`" and not ticks):
                scopes.append(state)
                ticks = ticks or entry == "`"
                before = None
            else:
                state = scopes.pop() if scopes else state
                ticks = ticks and entry != "`"
            continue
        after = entries[k + 1] if k + 1 < len(entries) else None
        was, before = before, None
        entry = _expand_env_split(entry)
        at, moves = _exec_index(entry)
        if at is None:
            continue
        name = _basename(entry[at][0])
        if name in ("cd", "chdir", "pushd", "popd"):
            if name == "popd":
                state = dirs.pop() if dirs else None
                continue
            args = [word[0] for word in entry[at + 1:] if not word[0].startswith("-") or word[0] == "-"]
            if name == "pushd":
                dirs.append(state)
            target = args[0] if args else ""
            if (state is None or not target or target == "-" or _LOOSE_CD_CHARS & set(target) or at
                    or moves or was in _CONDITIONAL_SEPARATORS or after in ("|", "&", "||")):
                state = None
            else:
                try:
                    state = [_resolve(here, target) for here in state][:64]
                    if not all(os.path.isdir(path) for path in state):
                        state = None          # the cd may fail, and the shell stays where it was
                except (OSError, ValueError):
                    state = None
            continue
        for code in _inline_codes(entry, at):
            # A wrapper that changes the folder, or code that changes its own (os.chdir,
            # process.chdir, Dir.chdir, chdir), leaves the folder uncertain.
            moved = moves or _CODE_CHDIR_RE.search(_blank_literals(code))
            found.append((code, None if moved else state))
    return found


def _code_literals(code: str) -> list[tuple[str, int, int]]:
    """The quoted strings in a piece of code (a doubled backslash read as one), each
    with where it starts and ends."""
    return [(next(group for group in match.groups() if group is not None).replace("\\\\", "\\"),
             match.start(), match.end()) for match in _CODE_LITERAL_RE.finditer(code)]


def _blank_literals(code: str) -> str:
    """The code with the inside of each quoted string blanked (same length)."""
    return _CODE_LITERAL_RE.sub(lambda m: m.group(0)[0] + "_" * (len(m.group(0)) - 2) + m.group(0)[-1], code)


def _removal_arguments(code: str) -> list[tuple[int, int]]:
    """Where the arguments of each call that removes or moves a path stand in the code:
    from the call's parenthesis to its matching one (a perl call without parentheses
    runs to the next `;`). Found on the code with its strings blanked, so a printed
    word such as 'rename' is no call and a parenthesis in a string does not count."""
    blank = _blank_literals(code)
    spans = []
    for call in _CODE_REMOVER_RE.finditer(blank):
        begin = call.end() - 1
        if blank[begin] == "(":
            depth, end = 0, len(blank)
            for at in range(begin, len(blank)):
                depth += (blank[at] == "(") - (blank[at] == ")")
                if depth == 0:
                    end = at
                    break
        else:
            end = blank.find(";", begin)
            end = len(blank) if end < 0 else end
        spans.append((begin, end))
    return spans


def _inline_code_names_record(code: str, cwds: list[Path]) -> bool:
    """True when a literal path in inline code names a protected record or the approval
    key, or (as an argument of a call that removes or renames a path) a folder that
    holds records, resolved from each cwd the way a command's path operands are.
    `print('clients')` names no path, `print('rename')` is no call, and a folder named
    beside a removal of something else is not its argument."""
    removals = _removal_arguments(code)
    for literal, begin, end in _code_literals(code):
        removed = any(first <= begin and end <= last for first, last in removals)
        if APPROVAL_KEY_RE.search(literal):
            return True
        for here in cwds:
            for path in _token_paths(literal, here)[0]:
                if APPROVAL_KEY_RE.search(path.as_posix()) or _protected_record(path):
                    return True
                if removed and _holds_records(path):
                    return True
    return False


def _command_targets_records(command: str, cwd: Path, strict: bool) -> bool:
    """True when a shell command, read by the build-only scan's own reader
    (_read_commands: nested bash -c and eval strings, substitutions, braces,
    ANSI-C quoting, values set earlier in the command, cd, pushd and popd), names
    a protected record or the approval key, or removes or moves a folder that
    holds records. A path built from a variable the command does not set names a
    record when one of the folders from cwd up to the workspace root, put in the
    variable's place, makes it one (as build-only reads such a path). After a
    change to a folder the gate cannot know ("$X", cd -), a command that removes
    or writes is refused when the workspace holds records at all."""
    roots: list[Path] | None = None

    def workspaces() -> list[Path]:
        nonlocal roots
        if roots is None:
            roots = _workspace_roots(cwd, strict)
        return roots
    # A grouping character (`$(...)`, a backtick, a brace the reader does not
    # expand) splits one command into several segments; the words after it are
    # still arguments of the remover before it, so a removal carries across until
    # a real separator. One entry per text being read: nested texts carry their own.
    carry: list[bool] = []
    codes: list[list[str]] = []       # per text: inline code run from an unknown folder
    seen: list[list[Path]] = []       # per text: the folders its segments run from
    for kind, item in _read_commands(command, cwd, cwd):
        if kind == "error":
            # The reader could not read a text (brace expansion past the length limit,
            # a PowerShell command it cannot decode), so it never yields that text's
            # segments. Raise into _approval_file_reason's handling: strict modes block;
            # full mode blocks when the call's text names a record (_text_names_record).
            raise ValueError(item)
        if kind == "text":
            carry.append(False)
            # Inline code (python -c, node -e, perl -e, ...) is read from the whole text,
            # quote-aware (the segment splitter cuts a `python -c "open('clients/...')"`
            # word apart at its ( and ;), by one reading that finds each interpreter in
            # its command's executable position (_exec_index: not `echo python -c x`),
            # takes its code and tracks the folder a cd leads to. A literal path in the
            # code that names a protected record or the approval key refuses the call in
            # every mode; code that names none passes in full mode, and `python -m torque`
            # has no such flag. Code run from a folder the text does not spell out is
            # checked from every folder the segments below report. Nested bash -c, eval
            # and powershell inner texts arrive here as their own text events.
            loose = []
            for code, folders in _inline_code_calls(item[0], list(item[1])):
                if folders is None:
                    loose.append(code)
                elif _inline_code_names_record(code, folders):
                    return True
            codes.append(loose)
            seen.append(list(item[1]))
        elif kind == "end":
            carry.pop()
            seen_cwds = seen.pop()
            for code in codes.pop():
                # Code run from a folder the text does not spell out (cd "$X"): checked
                # from every folder seen, and a literal that spells a record's own name
                # is refused when the workspace holds records, as a write is after such a cd.
                if _inline_code_names_record(code, seen_cwds) or (
                        any(_cf(re.split(r"[/\\]", lit)[-1].lstrip("<>+| ")) in _RECORD_NAMES for lit, _, _ in _code_literals(code))
                        and any(_holds_records(root) for root in workspaces())):
                    return True
        elif kind == "segment":
            toks, sep, cwds, unknown = item
            seen[-1] += [here for here in cwds if here not in seen[-1]]
            if _is_cached_git_rm(toks):
                # Untracking a record from git's index does not remove, write or even
                # read it: the file (and its content) stay exactly as they were.
                carry[-1] = False
                continue
            removes = carry[-1] or any(_basename(tok) in _REMOVERS for tok in toks)
            carry[-1] = removes and sep in _GROUPING_CHARS
            if removes:
                # A brace group the reader does not expand ({x,{y,clients}}) can leave
                # the word x,clients.
                toks = toks + [part for tok in toks if "," in tok for part in tok.split(",") if part]
            writes = removes or any(re.match(r"^(\d+|&)?>", tok) for tok in toks) \
                or any(_basename(tok) in _WRITE_VERBS for tok in toks)
            if unknown and writes and any(_holds_records(root) for root in workspaces()):
                return True
            for here in cwds:
                for tok in toks:
                    paths = _token_paths(tok, here)[0]
                    named = paths
                    if _PARAM_RE.search(_token_operand(tok)) and workspaces():
                        named = paths + _token_paths(tok, here, workspaces()[-1])[0]
                    if any(APPROVAL_KEY_RE.search(t.as_posix()) or _protected_record(t) for t in named):
                        return True
                    if removes and any(_holds_records(t) for t in paths):
                        return True
    return False


def _workspace_roots(start: Path, strict: bool = False) -> list[Path]:
    """The workspaces at or above start. When they cannot be read, none in full
    mode (best effort); with `strict` the error is raised, which blocks the call."""
    try:
        return [folder for folder, _mode, _known in _workspace_chain(start)]
    except OSError:
        if strict:
            raise
        return []


def _connected_workspaces(cwd: Path, touched: list[Path]) -> list[Path]:
    """Connected workspaces at or above a discovery start (_discovery_starts),
    found the same way as build-only ones."""
    found: list[Path] = []
    checked: set[str] = set()
    for start in _discovery_starts(cwd, touched):
        key = _cf(str(start))
        if key in checked:
            continue
        checked.add(key)
        try:
            chain = _workspace_chain(start)
        except OSError as exc:
            if _not_a_path(start, exc):
                continue
            raise
        found += [folder for folder, mode, _ in chain if mode == "connected" and folder not in found]
    return found


def main() -> int:
    # A hard limit on top of the cooperative checks: a daemon timer that blocks the
    # call and ends the process if the gate is still running after its budget.
    watchdog = _start_watchdog()
    try:
        return evaluate()
    finally:
        watchdog.cancel()


# The events after a call runs (D14). The same hook command handles all three.
POST_EVENTS = ("PostToolUse", "PostToolUseFailure")


def _post_event(event: dict) -> int:
    """Always 0: a hook after the call cannot undo it, so it never blocks."""
    try:
        # Imported only here: a PreToolUse call never loads it.
        from .execution import handle
        return handle(event, os.environ)
    except Exception as exc:
        print(f"Connected mode: the execution record could not be written ({exc}).", file=sys.stderr)
        return 0


def _hook_input():
    """The event as the host wrote it. Hosts write UTF-8; a redirected stdin on
    Windows decodes with the ANSI code page (and the hook's -I ignores PYTHONUTF8),
    which fails on some characters. Read the bytes when the stream has them."""
    raw = getattr(sys.stdin, "buffer", None)
    return raw.read() if raw is not None else sys.stdin.read()


def evaluate(raw: str | bytes | None = None) -> int:
    """Decide one hook event: `raw` when another host's adapter supplies it in
    Claude Code's form (gate_antigravity), otherwise standard input. Returns the
    exit code; the reason goes to stderr and an explicit allow or ask to stdout."""
    try:
        event = json.loads(_hook_input() if raw is None else raw)
        if not isinstance(event, dict):
            raise ValueError("hook input must be a JSON object")
        if event.get("hook_event_name") in POST_EVENTS:
            # F2: dispatch only on the parsed event name. A post-call event never
            # blocks (the call already ran); it only records how an approved call ended.
            return _post_event(event)
        if "tool_name" not in event:
            raise ValueError("hook input has no tool_name")
        cwd = Path(os.path.realpath(str(event.get("cwd") or ".")))
        tool_input = event.get("tool_input")
        if tool_input is None:
            tool_input = {}
        if not isinstance(tool_input, dict):
            raise ValueError("tool_input must be an object")
        with _call_budget():
            # Resolve the paths the call names once, and share them between the
            # build-only and connected workspace discovery (claude-A2a#4).
            touched = _touched_paths(tool_input, cwd)
            gated = _gated_workspaces(cwd, touched)
            allowed, reason = True, ""
            # Every build-only workspace from cwd upward applies; the strictest wins.
            for root in gated:
                allowed, reason = decide(str(event.get("tool_name", "")), tool_input, root, "build-only", cwd)
                if not allowed:
                    # When this workspace is build-only only because its config could not
                    # be read or understood, say so and how to fix it (claude-A2a#8).
                    problem = _mode_problem(root)
                    if problem:
                        reason = f"{reason} Note: {problem}"
                    break
            connected = _connected_workspaces(cwd, touched) if allowed and not gated else []
            if allowed and not gated and not connected:
                reason = _approval_file_reason(str(event.get("tool_name", "")), tool_input, cwd)
                allowed = not reason
            if connected:
                # Imported only here: a workspace without connected mode never loads it.
                from .gate_connected import ask_json, decide_connected
                from . import gate_connected as gc
                from . import launch as launches
                # Requirement 9: a session is bound to a client only by its launch record.
                results = [decide_connected(str(event.get("tool_name", "")), tool_input, root, cwd,
                                            env=launches.bound_env(os.environ, root),
                                            permission_mode=event.get("permission_mode"),
                                            session_id=event.get("session_id"),
                                            tool_use_id=event.get("tool_use_id"))
                           for root in connected]
                worst = max(results, key=lambda r: ("allow", "ask", "deny").index(r.action))
                if worst.action == "allow" and worst.approved:
                    # Tie the explicit allow to the root(s) whose own decision actually
                    # carries the approval id, not to "any connected root is unattended":
                    # a second, non-approving connected root's profile must never decide
                    # whether a different root's approval gets this explicit allow.
                    approving = [root for root, result in zip(connected, results) if result.approved]
                    if approving and all(gc.unattended(root) for root in approving):
                        print(gc.allow_json(f"Connected mode: approval {worst.approved} was used for this call."))
                        return 0
                if worst.action == "ask":
                    print(ask_json(worst.reason))
                    return 0
                allowed, reason = worst.action == "allow", worst.reason
    except BudgetExceeded as exc:
        print(_budget_reason(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        # Any failure here means this call could not be safely evaluated. Since
        # decide() only does real work in build-only mode (it returns immediately
        # otherwise), a failure here is only reachable when the mode could be
        # build-only, so this fails closed rather than letting the tool run.
        print(f"Build-only or connected mode: could not safely evaluate this call ({exc}); "
              "blocking to fail closed.", file=sys.stderr)
        return 2
    if not allowed:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
