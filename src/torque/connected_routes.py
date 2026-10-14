"""Classify one tool call into routes for connected mode.

Fail closed: an unknown Salesforce CLI verb, an unknown Salesforce MCP tool and
an unknown browser action count as writes, and a program this module does not
recognize counts as unverifiable (the host asks the consultant). A command that
prints or takes a credential, or lists every org on the machine, is its own kind,
refused whatever the session's approvals (SF_CREDENTIALS, SF_ALL_ORGS). Parsing is
quote-aware, so a SOQL string with parentheses or semicolons stays one argument;
command substitutions, `bash -c` strings and wrapped commands are classified too.
See docs/connected-approval.md for what this cannot see."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, replace
from functools import wraps
import os
import re
import shlex

from . import gate as g

# Route kinds, from least to most restricted in the gate. The last two are refused
# for the session whatever its approvals: a command that prints or takes a credential,
# and one that lists every org this machine is logged in to.
KINDS = ("local", "read", "check_only", "org_write", "browser_read", "browser_write", "unverifiable", "admin", "no_org",
         "credential", "all_orgs")
REFUSED_KINDS = ("credential", "all_orgs")
BROWSER_DATA_CLASSES = ("metadata", "records")

# sf commands that print a credential or take one, whatever their flags: an access
# token (`org display`, `org display user`), a password, a login. `org login` also
# points an alias at the org it logs in to. Matched on the command's words in any
# order and in the colon spelling, as the CLI accepts them; `auth` and `user` are the
# legacy topics (sfdx force:auth:web:login, force:user:display, force:user:password:generate).
# `org open` joins them when it prints its login URL (_prints_login_url).
SF_CREDENTIALS = {("org", "display"), ("org", "auth"), ("org", "login"), ("org", "generate", "password"),
                  ("user", "display"), ("user", "password"),
                  ("auth", "web"), ("auth", "jwt"), ("auth", "sfdxurl"), ("auth", "accesstoken"), ("auth", "device")}
# sf commands that list every org this machine is logged in to, other clients' orgs
# included (`org list --json` can print their access tokens too). `org list` with one
# of SF_ORG_LIST_ONE_ORG reads the named org only and is a read (SF_READ).
SF_ALL_ORGS = {("org", "list"), ("auth", "list"), ("alias", "list"), ("env", "list")}
SF_ORG_LIST_ONE_ORG = {"limits", "metadata", "metadata-types", "users", "sobject"}
# The full names of those commands. The CLI completes a shortened command on its own
# when only one command fits its words and flags (`sf display --verbose -o ALIAS` runs
# `org display`), so a command whose words all come from one of these names is
# refused as that command.
SF_REFUSED_NAMES = (
    ("credential", ("org", "display", "user")), ("credential", ("org", "generate", "password")),
    ("credential", ("org", "login", "web")), ("credential", ("org", "login", "jwt")),
    ("credential", ("org", "login", "sfdx-url")), ("credential", ("org", "login", "access-token")),
    ("credential", ("org", "login", "device")), ("credential", ("auth", "web", "login")),
    ("credential", ("auth", "jwt", "grant")), ("credential", ("auth", "sfdxurl", "store")),
    ("credential", ("auth", "accesstoken", "store")), ("credential", ("auth", "device", "login")),
    ("credential", ("user", "display")), ("credential", ("user", "password", "generate")),
    ("all_orgs", ("org", "list", "auth")), ("all_orgs", ("auth", "list")), ("all_orgs", ("alias", "list")),
    ("all_orgs", ("env", "list")))
# The words of `org open` and its subcommands, for the same reason.
SF_OPEN_WORDS = {"org", "open", "agent", "authoring-bundle"}
# `sf help org display` and `sf which org display` describe a command; they do not run it.
SF_DESCRIBE_ONLY = {"help", "which"}
# `org open` flags that print the login URL, and its short options that take a value
# (in a group such as -ro ALIAS, a value option ends the options).
SF_OPEN_URL_FLAGS = {"--url-only", "--urlonly", "--json", "--flags-dir"}
SF_OPEN_VALUE_LETTERS = "obpfu"
# In container mode the CLI prints the login URL instead of opening a browser. The
# hook inherits the session's environment, so the gate reads the variables there
# (_container_mode), and refuses a command that sets one (classify_bash).
SF_CONTAINER_VARS = ("SF_CONTAINER_MODE", "SFDX_CONTAINER_MODE")
_CONTAINER_SET_RE = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(SF_CONTAINER_VARS) + r")['\"]?\s*=", re.IGNORECASE)
# Characters the shell expands before sf runs: a variable or brace expansion can
# become any word, a glob any file name in the folder.
_EXPANSION_CHARS = frozenset("$`{}")
_GLOB_CHARS = frozenset("*?[")
# sf commands that only read, by their leading topic words (docs/connected-approval.md,
# "Host facts verified"). Everything else with an org flag is a write.
SF_READ = {
    ("data", "query"), ("data", "get", "record"), ("data", "search"), ("data", "export"),
    ("data", "bulk", "results"), ("data", "resume"),  # resume reports a job and can return its rows
    ("sobject", "describe"), ("sobject", "list"),
    ("org", "list", "limits"), ("org", "list", "metadata"), ("org", "list", "metadata-types"),
    ("org", "list", "users"), ("org", "list", "sobject"),
    ("project", "retrieve", "start"), ("project", "retrieve", "preview"),
    ("project", "deploy", "report"), ("project", "deploy", "preview"),
    ("apex", "get", "log"), ("apex", "list", "log"), ("apex", "get", "test"),
    ("flow", "get", "test"), ("logic", "get", "test"),
    ("package", "installed", "list"), ("package", "install", "report"), ("package", "uninstall", "report"),
    ("package", "version", "list"), ("community", "list", "template"), ("limits", "api", "display"),
    ("cmdt", "generate", "fromorg"),
}
# Reads of record data and of debug logs, which the client's consent must cover.
# `cmdt generate fromorg` exports every record of an object to files, and `org list
# users` lists the org's people.
SF_RECORD_READS = {("data", "query"), ("data", "get", "record"), ("data", "search"), ("data", "export"),
                   ("data", "bulk", "results"), ("data", "resume"),
                   ("cmdt", "generate", "fromorg"), ("org", "list", "users")}
SF_LOG_READS = {("apex", "get", "log"), ("apex", "list", "log")}
# The full names of the commands that read records or logs. The CLI takes a command's
# words in any order (`sf query data`), so a command with exactly the words of one of
# these is that read. It also offers to complete a shortened one (`sf query`), so a
# command the gate does not know whose words all come from one of these names is
# refused: an approval for it would stand in for the consent that read needs.
SF_READ_NAMES = (
    ("records", ("data", "query")), ("records", ("data", "query", "resume")),
    ("records", ("data", "soql", "query")),  # the legacy name, which the CLI also takes as separate words
    ("records", ("data", "get", "record")), ("records", ("data", "search")),
    ("records", ("data", "export", "tree")), ("records", ("data", "export", "bulk")),
    ("records", ("data", "export", "resume")), ("records", ("data", "bulk", "results")),
    ("records", ("data", "resume")), ("records", ("cmdt", "generate", "fromorg")),
    ("records", ("org", "list", "users")),
    ("debug_logs", ("apex", "get", "log")), ("debug_logs", ("apex", "list", "log")),
    ("debug_logs", ("apex", "tail", "log")))
SF_CHECK_ONLY = {("project", "deploy", "validate"), ("apex", "run", "test"), ("flow", "run", "test"),
                 ("logic", "run", "test")}
# Local file generation and tooling; they take no org.
SF_LOCAL = {("project", "generate"), ("project", "convert"), ("project", "list", "ignored"),
            ("template", "generate"), ("schema", "generate"), ("cmdt", "generate"), ("lightning", "generate"),
            ("apex", "generate"), ("agent", "generate"),
            ("version",), ("help",), ("commands",), ("whatsnew",), ("which",), ("search",), ("info",),
            ("doctor",), ("autocomplete",), ("config", "list"), ("config", "get"),
            ("plugins",), ("plugins", "inspect")}
# Tooling and sessions the gate cannot tell apart by name. `org open` is not here:
# it mints a session URL (an org write, D17), so it falls through to the default
# rule below, "anything else with an org flag is a write", unless it prints that URL
# (SF_CREDENTIALS). `org login` is not here either: it is refused (SF_CREDENTIALS).
SF_ASK = {("code-analyzer",), ("dev",), ("lightning", "dev"),
          ("org", "logout"), ("plugins", "install"), ("plugins", "link"),
          ("plugins", "update"), ("plugins", "uninstall"), ("plugins", "reset"), ("update",)}
# Changing an alias or the default org would move an approved command to another org.
SF_ADMIN = {("alias", "set"), ("alias", "unset"), ("config", "set"), ("config", "unset")}
SFDX_RECORD_READS = {"force:data:soql:query"}
SFDX_LOG_READS = {"force:apex:log:get", "force:apex:log:list"}
SFDX_READ = {"force:data:soql:query", "force:source:retrieve", "force:mdapi:retrieve",
             "force:schema:sobject:describe", "force:schema:sobject:list",
             "force:apex:log:get", "force:apex:log:list"}
TORQUE_ORG_FLAGS = set(g.ORG_FLAGS) | {"--org"}
# The long options the gate decides by. The parsers of the delegated packages (`torque
# logs`, `advisory`, `deploy` ...) take a prefix of an option's name as that option
# (`--target other` is `--target-org other`, and the last one given wins), so a name cut
# short would let the command use another org, client, filter or mode than the one the
# gate read. Torque's own parsers have abbreviations off and refuse such a name
# themselves (cli._disable_abbreviations); the gate refuses it for every Torque command,
# which for those is only the cautious side.
TORQUE_DECIDING_OPTIONS = frozenset({name for name in TORQUE_ORG_FLAGS if name.startswith("--")} | {
    "--workspace", "--client", "--initiative", "--write", "--dry-run", "--where", "--headed", "--capture-before",
    "--capture-before-record", "--capture-before-metadata", "--record", "--help", "--version"})
# The options whose value the gate decides by: which org, client or initiative. (A guarded
# read's `--workspace` is compared as a path with the workspace the session works in, and
# is held to the same rule there: _torque.)
TORQUE_VALUE_DECIDES = frozenset(TORQUE_ORG_FLAGS | {"--client", "--initiative"})
# A tilde the shell expands to something else than the home folder: `~+` (the current
# folder), `~-` (the one before), `~2` (the folder stack), `~name` (another user's home).
_SHELL_TILDE = re.compile(r"(?:--workspace=)?~[^/\\]")
# torque commands another package's parser reads (cli.DELEGATES and cli.PUBLIC_ROUTES).
# Everything else is read by Torque's own parser, which the gate follows exactly.
TORQUE_DELEGATED = frozenset({"advisory", "qa", "revert", "logs", "browser", "meeting", "lesson", "probes",
                              "ai-regression", "deploy", "data", "org", "recover"})
# The installed scripts run the same delegates, so their reads need the same consent.
TORQUE_CONSOLE_ROUTES = {
    "jsc": "revert", "jsc-advisory": "advisory", "jsc-qa": "qa", "jsc-browser-tests": "browser",
    "jsc-memory": "lesson", "jsc-loganalyzer": "logs", "jsc-probes": "probes",
    "meeting-processor": "meeting", "jsc-ai-prompt-regression": "ai-regression"}
# Options of Torque's own commands that take exactly one value wherever they exist
# (tests/test_gate_fixes_a21.py checks this against the parser). Only after one of
# these, or attached to an option with `=`, is a double-quoted variable a value and
# nothing else: after a flag that takes no value it could become another option.
TORQUE_VALUE_OPTIONS = frozenset({
    "--account", "--agreed-on", "--approval", "--approver-uid", "--audit-trail", "--before-state",
    "--before-state-object", "--binding", "--capture-before-metadata", "--capture-before-record", "--change",
    "--client", "--component", "--criterion", "--data", "--evidence", "--for", "--hook-python", "--host",
    "--idempotency-key", "--initiative", "--input", "--job-id", "--kind", "--manifest", "--manual-recovery",
    "--mcp", "--metadata", "--minutes", "--model-id", "--name", "--new-component", "--org", "--outcome",
    "--output", "--owner", "--path", "--payload-digest", "--profile", "--purpose", "--reason", "--reason-class",
    "--record", "--request-sha256", "--result", "--review-date", "--reviewer", "--role", "--since", "--status",
    "--summary", "--suspend-contact", "--text", "--title", "--uid", "--validated-job", "--verify", "--wait",
    "--workspace",
    # torque guarded, and --org-data of `client consent record`
    "--as", "--child", "--field", "--fields", "--group-by", "--id", "--limit", "--note", "--object", "--org-data",
    "--target-org", "--value", "--where", "--with-children"})
# torque guarded: the lanes that read an org and the consent class each needs (None:
# metadata), its local views, and everything else, which only the consultant runs.
GUARDED_READS = {("counts",): "counts", ("fill-rate",): "counts", ("config",): "config_records",
                 ("record",): "test_records", ("related",): "test_records", ("org",): None,
                 ("policy", "candidates"): None}
GUARDED_LOCAL = {("exposure",), ("policy", "show"), ("test-records", "list")}
# torque routes that change an org, and their subcommands that only read.
TORQUE_WRITE_ROUTES = {"deploy", "data", "org", "recover", "revert"}
REVERT_READ = {"show", "preview", "discard", "post-deploy"}
TORQUE_READ_ROUTES = {"advisory", "logs", "inspect"}
TORQUE_BROWSER_ROUTES = {"browser", "qa"}
TORQUE_WRITE_ELSE = {"probes", "ai-regression"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
# Interpreters, runners and other Salesforce tools. Any program not in
# LOCAL_COMMANDS is unverifiable already; these name the common cases.
INTERPRETERS = re.compile(r"^(python[0-9.]*|pythonw|py|pypy[0-9.]*|node|nodejs|deno|bun|ruby|irb|perl|php|lua|"
                          r"tclsh|wish|pwsh|powershell|cmd|osascript|jshell|groovy|scala|R|Rscript|julia|swift|"
                          r"expect|java|dotnet|go|cargo|rustc|gcc|cc|clang|make|gmake)$", re.IGNORECASE)
RUNNERS = {"eval", "source", ".", "xargs", "parallel", "watch", "npx", "npm", "pnpm", "yarn", "bunx", "just",
           "invoke", "rake", "gradle", "gradlew", "mvn", "ant", "tox", "nox", "poetry", "pipenv", "uv", "uvx",
           "pipx", "hatch", "pytest", "py.test", "jest", "vitest", "mocha", "sudo", "doas", "su", "ssh",
           "docker", "podman", "kubectl", "script", "tmux", "screen", "direnv", "op", "dotenv", "chronic",
           "unbuffer", "flock", "setsid", "open", "xdg-open", "start", "entr", "fswatch", "launchctl",
           "crontab", "at", "batch", "trap"}
# Wrappers that run the rest of their line as a command.
BENIGN_WRAPPERS = {"command", "builtin", "exec", "time", "nice", "nohup", "stdbuf", "timeout", "caffeinate",
                   "noglob", "nocorrect"}
# Other programs that can write to a Salesforce org on their own.
SALESFORCE_TOOLS = {"cci", "cumulusci", "force", "vlocity", "jsforce", "sfdmu", "sfp", "sfpowerscripts",
                    "sfdx-hardis", "hardis"}
NETWORK = {"curl", "wget", "http", "https", "xh", "httpie", "aria2c"}
SF_HOSTS = re.compile(r"(salesforce\.com|force\.com|salesforce-setup\.com|cloudforce\.com|database\.com|"
                      r"site\.com|salesforce-sites\.com|visualforce\.com|lightning\.com|sfdc\.net)",
                      re.IGNORECASE)
# Project hooks, plugins, executable configuration and pagers are not visible in
# the command line. None of these programs is verified merely by its name.
EXTENSIBLE_COMMANDS = {
    "git", "gh", "hg", "ruff", "black", "isort", "mypy", "flake8", "pylint", "prettier", "eslint",
    "shellcheck", "tsc", "less", "more", "man", "info", "tar", "zip", "unzip", "rsync", "scp",
    "rg", "ag", "ack", "fd", "sort", "split", "sed", "awk", "gawk", "mawk", "nawk",
}
# Programs that only read or change local files. Their arguments are data, so an
# `sf` word inside them (grep sf, echo sf) is not a call.
LOCAL_COMMANDS = {
    "ls", "cat", "head", "tail", "wc", "grep", "egrep", "fgrep", "tree", "echo",
    "printf", "pwd", "cd", "pushd", "popd", "dirs", "mkdir", "rmdir", "touch", "cp", "mv", "rm", "ln", "chmod",
    "chown", "diff", "cmp", "comm", "uniq", "cut", "tr", "paste", "join", "fold", "fmt", "nl", "rev",
    "jq", "yq", "file", "stat", "du", "df", "date", "cal", "basename", "dirname", "realpath",
    "readlink", "which", "type", "whereis", "test", "[", "[[", "true", "false", ":",
    "gzip", "gunzip", "zcat", "bzip2", "xz", "shasum", "sha1sum", "sha256sum", "md5", "md5sum", "cksum",
    "xmllint", "column", "tee", "sleep", "export", "unset", "set", "shift", "read", "exit", "return", "local",
    "declare", "typeset", "readonly", "alias", "unalias", "hash", "wait", "jobs", "fg", "bg", "kill", "ps",
    "top", "uptime", "whoami", "id", "hostname", "uname", "sw_vers", "env", "printenv", "locale", "tput",
    "clear", "history", "help", "patch", "iconv", "base64", "xxd", "od",
    "hexdump", "strings", "csplit", "mktemp", "truncate", "dd",
    "find", "ditto", "pbcopy", "pbpaste", "say",
    "curl", "wget", "http", "https", "xh", "httpie", "aria2c", "ping", "dig", "nslookup", "host",
}
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_KEYWORDS = {"{", "}", "!", "if", "then", "else", "elif", "fi", "do", "done", "while", "until", "for", "in",
             "case", "esac", "select", "function", "coproc", "time"}
_SEPARATOR_CHARS = set(";|()\n`")
_PUNCTUATION = "();<>|&`\n"
MCP_ORG_KEYS = ("usernameOrAlias", "targetOrg", "target_org", "target-org", "orgAlias", "org_alias", "org",
                "alias", "username", "targetUsername", "target_username")
MCP_WRITE_WORDS = {"deploy", "create", "update", "upsert", "delete", "insert", "execute", "exec", "assign",
                   "install", "uninstall", "write", "dml", "anonymous", "merge", "publish", "activate",
                   "deactivate", "reset", "remove", "set", "save", "patch", "post", "put", "import", "load",
                   "push", "apply", "revert", "undelete", "cancel", "abort", "grant", "revoke", "enable",
                   "disable", "schedule", "commit", "modify", "edit", "rename", "add", "upload", "send", "login",
                   "logout", "open"}
MCP_RECORD_WORDS = {"query", "soql", "sosl", "search", "record", "records", "row", "rows", "data", "export",
                    "sobject", "sobjects", "rest", "api", "request", "graphql", "composite", "report", "reports",
                    "dashboard", "file", "files", "attachment", "content"}
MCP_READ_WORDS = {"query", "soql", "sosl", "describe", "list", "get", "retrieve", "search", "display", "read",
                  "show", "find", "fetch", "count", "inspect", "view", "status", "info", "explain", "analyze",
                  "resume", "report", "limits", "whoami"}
# A Salesforce MCP tool whose name says it returns a credential, or lists every org.
MCP_CREDENTIAL_WORDS = {"token", "tokens", "password", "passwords", "credential", "credentials", "secret",
                        "secrets", "frontdoor"}
# Which MCP servers drive a browser or the desktop is read from the gate
# (gate._mcp_surface), which build-only mode uses too; these words then sort a
# browser tool into a read or a change.
BROWSER_READ_WORDS = {"read", "get", "find", "screenshot", "snapshot", "list", "navigate", "tabs", "tab",
                      "context", "console", "network", "messages", "message", "page", "pages", "text", "zoom",
                      "wait", "resize", "cursor", "position", "gif", "lighthouse", "performance", "trace",
                      "heapsnapshot", "heap", "take", "query", "switch", "select", "connected", "browsers",
                      "browser", "requests", "request", "logs", "log", "mcp", "audit", "analyze", "insight",
                      "start", "stop", "close", "granted", "applications", "for", "display", "emulate", "new",
                      "create", "screen", "capture", "dump", "accessibility", "tree", "shortcuts"}
COMPUTER_READ_ACTIONS = {"screenshot", "zoom", "wait", "cursor_position", "scroll", "scroll_to", "mouse_move",
                         "get_page_text", "read_page", "find"}


@dataclass(frozen=True)
class Route:
    kind: str
    org: str | None
    detail: str
    client: str | None = None
    data: str | None = None  # reads default to metadata; records and debug_logs need their own consent
    headed: bool = False  # a browser route asking for a visible browser (--headed or a prefix of it)
    workspace: str | None = None  # a guarded read's --workspace, which must be the workspace the gate governs


def _flag_values(args: list[str], names) -> list[str]:
    values = []
    for i, tok in enumerate(args):
        if tok in names and i + 1 < len(args):
            values.append(args[i + 1])
        elif "=" in tok and tok.split("=", 1)[0] in names and tok.startswith("--"):
            values.append(tok.split("=", 1)[1])
    return values


def org_values(args: list[str], names=g.ORG_FLAGS) -> list[str]:
    return list(dict.fromkeys(_flag_values(args, names)))


def org_value(args: list[str]) -> str | None:
    values = org_values(args)
    return values[0] if len(values) == 1 else None


# How a word was written, which the gate needs after the quotes are gone. Outside
# quotes the shell expands `$`, a backtick, a brace and a glob character, and the
# result can be several words or an option. Inside double quotes `$` and a backtick
# still fill in text, but as one word. Inside single quotes, after a backslash, and
# for the other characters inside double quotes, they are plain text. So is an
# operator character there: `--hook-python ">"` passes the text `>`, it redirects
# nothing. _mask replaces the plain ones and the filled-in ones by private marks
# before the words are split, and drops a backslash at a line end with the line end
# (outside single quotes), as Bash does: `work\<newline>space` is `workspace`.
_PLAIN = {c: chr(0xE000 + i) for i, c in enumerate("$`{}*?[@();<>|&\n#,\r")}
_FILLED = {c: chr(0xE100 + i) for i, c in enumerate("$`<>")}
# Inside a comment or a here-document's body the shell takes nothing as quoting: the
# quotes and backslashes there are marked, so the splitter does not pair them with real
# ones (`echo a # it's` followed by a line with another apostrophe).
_INERT = {c: chr(0xE200 + i) for i, c in enumerate("'\"\\")}
_UNMASK = {ord(mark): c for table in (_PLAIN, _FILLED, _INERT) for c, mark in table.items()}
_LITERAL_MARK = re.compile(r"\uefff([0-9a-f]{4})")
_WORD_BREAK = frozenset(" \t\r\n;|&()<>")
_HEREDOC_OPERATOR = re.compile(r"<(?:\\\n)*<(?!(?:\\\n)*<)")
class _Delimiter(str):
    quoted: bool

    def __new__(cls, value: str, quoted: bool) -> _Delimiter:
        delimiter = str.__new__(cls, value)
        delimiter.quoted = quoted
        return delimiter


def _heredoc(command: str, i: int) -> tuple[str, bool] | None:
    """(delimiter, leading tabs dropped) of the here-document the `<<` at `i` opens, or
    None when no word follows it. The delimiter is the word after `<<` with its quoting
    removed, as Bash takes it, whatever characters it holds: `<<E`, `<<-E`, `<< 'E F'`,
    `<<"E"`, `<<\\E`, `<<\\!`, `<<E"O"F`, `<<@E@`. Nothing in it is expanded."""
    operator = _HEREDOC_OPERATOR.match(command, i)
    if operator is None:
        return None
    before = i
    while before >= 2 and command[before - 2:before] == "\\\n":
        before -= 2
    if before and command[before - 1] == "<":
        return None
    j, size = operator.end(), len(command)
    while command.startswith("\\\n", j):
        j += 2
    tabs = command[j:j + 1] == "-"
    j += tabs
    while command[j:j + 1] in (" ", "\t") or command.startswith("\\\n", j):
        j += 2 if command.startswith("\\\n", j) else 1
    out, start, quoted = [], j, False
    while j < size and command[j] not in " \t\r\n;|&()<>":
        c = command[j]
        if c == "$" and command[j + 1:j + 2] == "'":
            quoted = True
            stop = _closing(command, j + 2, "'")
            if stop is None:
                return None
            out.append(_ansi_c(command[j + 2:stop - 1]))
            j = stop
        elif c == "$" and command[j + 1:j + 2] == '"':
            j += 1
        elif c == "'":
            quoted = True
            stop = command.find("'", j + 1)
            stop = size if stop < 0 else stop
            out.append(command[j + 1:stop])
            j = stop + 1
        elif c == '"':
            quoted = True
            j += 1
            while j < size and command[j] != '"':
                if command.startswith("\\\n", j):
                    j += 2
                    continue
                if command[j] == "\\" and command[j + 1:j + 2] in ('"', "\\", "$", "`"):
                    j += 1
                out.append(command[j])
                j += 1
            j += 1
        elif c == "\\" and j + 1 < size:
            if command[j + 1] != "\n":
                quoted = True
                out.append(command[j + 1])
            j += 2
        else:
            out.append(c)
            j += 1
    return (_Delimiter("".join(out), quoted), bool(tabs)) if j > start else None
_FILLED_MARKS = frozenset(_FILLED.values())
_LOOSE_CHARS = _EXPANSION_CHARS | _GLOB_CHARS


def _substitution_starts(command: str) -> list[tuple[int, str]]:
    """(index, mark) for every command substitution the shell would run in the line:
    the `$` of a `$(` or an opening backtick, outside single quotes, ANSI-C strings
    and comments, and also inside the body of a here-document (where the shell
    expands them unless the delimiter is quoted). From the same pass as _mask."""
    return _scan(command)[1]


def _substitutions(command: str) -> list[str]:
    """The text of every command substitution in the line (`$(...)` and backticks),
    each read to its own end with its own quotes. One that does not close runs to the
    end of the line."""
    found = []
    for i, mark in _substitution_starts(command):
        syntax = mark.replace("\\\n", "")
        if syntax.startswith("${"):
            continue            # an expansion, kept for the net (_substitution_tails)
        stop = (_substitution_end(command, i) if syntax in ("$(", "<(", ">(")
                else _closing(command, i + 1, "`"))
        text = command[i + len(mark):len(command) if stop is None else stop - 1]
        found.extend(_backtick_readings(text) if mark == "`" else [text])
    return [text for text in found if text.strip()]


_BACKTICK_ESCAPE = re.compile(r"\\([$`\\])")
_BACKTICK_ESCAPE_QUOTED = re.compile(r"\\([$`\\\"])")


def _backtick_readings(text: str) -> list[str]:
    """The text between two backticks as Bash can read it. Before it reads that text as a
    command, Bash takes one backslash off in front of a `$`, a backtick and a backslash,
    and in front of a double quote too when the backticks stand inside double quotes
    (checked in real Bash): ``echo `echo \\\\"x; sf data query ... #\\\\"` `` runs the query,
    because `\\\\"` has become `\\"`, a quote that opens nothing. `$(...)` takes nothing
    off. Each of these is a reading, the text as written among them, and each has to pass."""
    if "\\" not in text:
        return [text]
    return list(dict.fromkeys([text, _BACKTICK_ESCAPE.sub(r"\1", text), _BACKTICK_ESCAPE_QUOTED.sub(r"\1", text)]))


def _substitution_tails(command: str) -> list[str]:
    """Everything after the start of each substitution, to the end of the line, and
    after the start of each substitution inside it. Reading these as command text is
    the net under _substitution_end: wherever a substitution really ends, a command
    inside it lies in its tail, where it begins a fresh quoting context.
    The same for a `${...}` expansion whose word has quoting of its own (the net
    under _parameter_end): wherever it really ends, it ends at a `}`, and what
    follows that brace is text of the line again, inside double quotes when the
    expansion stood in them."""
    tails, seen, queue = [], set(), [command]
    while queue and len(tails) < 64:
        text = queue.pop()
        for i, mark in _substitution_starts(text):
            if mark.replace("\\\n", "").startswith("${"):
                found, k = [], text.find("}", i)
                while k >= 0 and len(found) < 6:
                    found.append(('"' if mark.endswith('"') else "") + text[k + 1:])
                    k = text.find("}", k + 1)
            elif mark == "`":
                # Its closing backtick would read as the start of another substitution.
                stop = _closing(text, i + 1, "`")
                after = "" if stop is None else " ; " + text[stop:]
                found = [reading + after
                         for reading in _backtick_readings(text[i + 1:] if stop is None else text[i + 1:stop - 1])]
            else:
                found = [text[i + len(mark):]]
            for tail in found:
                if tail.strip() and tail not in seen:
                    seen.add(tail)
                    tails.append(tail)
                    queue.append(tail)
    return tails


def _literal(text: str) -> str:
    """Keep the shell's original private-use characters apart from the reader's marks."""
    return "".join("\uefff" + f"{ord(c):04x}" if "\ue000" <= c <= "\uf8ff" else c for c in text)


def _plain_word(text: str) -> str:
    """Text as one single-quoted piece for the splitter, every special character marked plain."""
    return "'" + "".join(_PLAIN.get(c, c) for c in _literal(text)).replace("'", "'\\''") + "'"


def _inert(text: str) -> str:
    return "".join(_INERT.get(c, c) for c in _literal(text))


def _sealed(text: str) -> str:
    """Text inside a double-quoted word that the splitter must take whole: a command
    substitution, with quotes of its own."""
    return "".join(_INERT.get(c) or _PLAIN.get(c, c) for c in _literal(text))


_ANSI_C = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v",
           "\\": "\\", "'": "'", '"': '"', "?": "?"}


def _ansi_c(text: str) -> str:
    """The text of a $'...' word as Bash decodes it: the letter escapes, one to three
    octal digits, \\x with one or two hex digits, \\u with up to four and \\U with up
    to eight, \\cX; an escape Bash does not know keeps its backslash; and a NUL ends
    the text, as it does in Bash."""
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


_CASE_WORD = re.compile(r"(case|esac)(?![A-Za-z0-9_./-])")


def _closing(command: str, j: int, close: str) -> int | None:
    """The index just past the `close` character that ends what starts at `j` (a `${`
    expansion, an ANSI-C string, a backtick span), skipping escaped characters."""
    while j < len(command) and command[j] != close:
        j += 2 if command[j] == "\\" else 1
    return j + 1 if j < len(command) else None


_PARAMETER_NAME = re.compile(r"[!#]?(?:[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[@*#?$!-])(?:\[[^\]\n]*\])?")


def _after_continuations(command: str, i: int) -> int:
    while command.startswith("\\\n", i):
        i += 2
    return i


def _before_continuations(command: str, i: int) -> str:
    while i >= 2 and command[i - 2:i] == "\\\n":
        i -= 2
    return command[i - 1:i]


def _case_word(command: str, i: int) -> tuple[str, int] | None:
    for word in ("case", "esac"):
        j = i
        for letter in word:
            if command[j:j + 1] != letter:
                break
            j = _after_continuations(command, j + 1)
        else:
            if not re.match(r"[A-Za-z0-9_./-]", command[j:j + 1]):
                return word, j
    return None


def _parameter_end(command: str, i: int, quoted: bool) -> int | None:
    """The index just past the `}` that closes the `${` at `i`, or None when it does not
    close. Bash reads the word inside the braces with quoting of its own, also when
    the expansion stands in double quotes (`"${x:-"'"}"` is one word): a backslash, a
    double-quoted piece (in which a `}` ends nothing), an ANSI-C string, another
    expansion, a command substitution, and single quotes. In a double-quoted
    expansion whose operator takes a word (`:-`, `:=`, `:+`, `:?`, with or without
    the colon) a single quote is an ordinary character; with a pattern operator
    (`#`, `%`, `/`) it quotes, as it does outside double quotes."""
    j, size = _after_continuations(command, i + 1) + 1, len(command)
    logical = command[j:].replace("\\\n", "")
    name = _PARAMETER_NAME.match(logical)
    operator = logical[name.end() if name else 0:][:2]
    plain_quote = quoted and (operator[:1] in ("-", "=", "+", "?") or (operator[:1] == ":" and operator[1:2] in
                                                                         ("-", "=", "+", "?")))
    inner = False           # inside a double-quoted piece of the word
    while j < size:
        c = command[j]
        after = _after_continuations(command, j + 1)
        follower = command[after:after + 1]
        if c == "\\":
            j += 2
            continue
        if c == "$" and follower == "(":
            stop = _substitution_end(command, j)
        elif c == "$" and follower == "{":
            stop = _parameter_end(command, j, quoted or inner)
        elif c == "`":
            stop = _closing(command, j + 1, "`")
        elif c == "$" and follower == "'" and not inner and not plain_quote:
            stop = _closing(command, after + 1, "'")
        elif c == "'" and not inner and not plain_quote:
            stop = command.find("'", j + 1) + 1 or None
        else:
            if c == '"':
                inner = not inner
            elif c == "}" and not inner:
                return j + 1
            j += 1
            continue
        if stop is None:
            return None
        j = stop
    return None


# What stands directly before a `(` that is part of a word: a substitution (`$(`), a process
# substitution (`<(`, `>(`) or an array (`x=(`). After its `)` the same word goes on, so a `#`
# there begins no comment (`echo $(date)#x ; sf ...` runs sf; checked in real Bash). After the
# `)` of a subshell or of a `case` pattern a new word begins.
_JOINING = ("$", "<", ">", "=")


def _substitution_end(command: str, i: int) -> int | None:
    """The index just past the `)` that closes the `$(` at `i`, or None when it does
    not close. The substitution is read as Bash reads it: with quotes of its own, and
    with the places where a `)` does not end it: inside quotes, an ANSI-C string or a
    `${...}` expansion, after a backslash, in a comment, in the body of a
    here-document, and at the end of a `case` pattern."""
    depth, quote, j, size = 0, "", i + 1, len(command)
    word_start, cases, opened = False, [], []
    joins: list = []        # for each open parenthesis: the word it stands in goes on after it closes
    while j < size:
        c = command[j]
        after = _after_continuations(command, j + 1)
        follower = command[after:after + 1]
        if quote == "'":
            quote = "" if c == "'" else quote
            word_start = False
        elif c == "\\":
            j += 1
            word_start = False
        elif c == "$" and follower == "(":
            inner = _substitution_end(command, j)       # a substitution inside this one, quoted or not
            if inner is None:
                return None
            j, word_start = inner, False
            continue
        elif c == "$" and follower == "{":
            inner = _parameter_end(command, j, bool(quote))     # an expansion, with quotes of its own
            if inner is None:
                return None
            j, word_start = inner, False
            continue
        elif c == "`":
            inner = _closing(command, j + 1, "`")
            if inner is None:
                return None
            j, word_start = inner, False
            continue
        elif quote:
            quote = "" if c == '"' else quote
            word_start = False
        elif c == "#" and word_start:
            j = command.find("\n", j)                   # a comment: nothing in it ends anything
            if j < 0:
                return None
            continue
        elif c == "\n" and opened:
            j += 1
            for delimiter, tabs in opened:              # the bodies of the here-documents opened on this line
                while j < size:
                    stop = command.find("\n", j)
                    stop = size if stop < 0 else stop
                    line = command[j:stop]
                    j = min(stop + 1, size)
                    if (line.lstrip("\t") if tabs else line).rstrip("\r") == delimiter:
                        break
            opened, word_start = [], True
            continue
        elif c == "$" and follower == "'":
            inner = _closing(command, after + 1, "'")
            if inner is None:
                return None
            j, word_start = inner, False
            continue
        elif word_start and _case_word(command, j):
            word = _case_word(command, j)
            if word[0] == "case":
                cases.append(depth)
            elif cases:
                cases.pop()
            j, word_start = word[1], False
            continue
        else:
            if c == "<":
                found = _heredoc(command, j)
                if found:
                    opened.append(found)
            word_start = c in _WORD_BREAK
            if c in "'\"":
                quote = c
            elif c == "(":
                depth += 1
                joins.append(_before_continuations(command, j) in _JOINING)
            elif c == ")" and not (cases and cases[-1] == depth):       # not the end of a case pattern
                depth -= 1
                if depth == 0:
                    return j + 1
                if joins.pop():
                    word_start = False      # it closed a process substitution or an array: the word goes on
        j += 1
    return None


def _mask(command: str, _join: bool = False) -> str:
    return _scan(command, _join)[0]


_BODY_SUBSTITUTION = re.compile(r"(?<!\\)(\$(?:\\\n)*\(|`)")
# A file descriptor's digits, directly before a redirection. A line continuation between
# them, or between them and the operator, is no gap for Bash (`1\<newline>>&2` is `1>&2`).
_FILE_DESCRIPTOR = re.compile(r"[0-9](?:[0-9]|\\\n)*(?=[<>])")
_NAMED_DESCRIPTOR = re.compile(r"\{(?:\\\n)*[A-Za-z_](?:[A-Za-z0-9_]|\\\n)*(?=[\[}])")
_NAIVE_DESCRIPTOR = re.compile(r"(?<![^\s;|&()])[0-9]+(?=[<>])")
_QUOTING = frozenset("'\"\\")


def _descriptor_end(command: str, i: int) -> int | None:
    """The end of a {name} or {array[index]} descriptor before a redirection."""
    name = _NAMED_DESCRIPTOR.match(command, i)
    if name is None:
        return None
    j, size = name.end(), len(command)
    if command[j:j + 1] == "[":
        j, depth, quote = j + 1, 1, ""
        while j < size and depth:
            c = command[j]
            after = _after_continuations(command, j + 1)
            follower = command[after:after + 1]
            if quote == "'":
                quote = "" if c == "'" else quote
            elif c == "\\":
                j += 2
                continue
            elif (((c == "$" or (c in "<>" and not quote)) and follower == "(")
                  or (c == "$" and follower == "{")):
                stop = _parameter_end(command, j, bool(quote)) if follower == "{" else _substitution_end(command, j)
                if stop is None:
                    return None
                j = stop
                continue
            elif c == "`" or (c == "$" and follower == "'" and not quote):
                stop = _closing(command, after + 1 if c == "$" else j + 1, "'" if c == "$" else "`")
                if stop is None:
                    return None
                j = stop
                continue
            elif quote:
                quote = "" if c == '"' else quote
            elif c in "'\"":
                quote = c
            elif c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
            elif c in _WORD_BREAK and c != "\r":
                return None
            j += 1
        if depth:
            return None
    if command[j:j + 1] != "}":
        return None
    j += 1
    while command.startswith("\\\n", j):
        j += 2
    return j if command[j:j + 1] in ("<", ">") else None


def _scan(command: str, _join: bool = False, _tails: list[str] | None = None) -> tuple[str, list, int | None, int]:
    """(the command with its plain, filled-in and inert characters marked (see above),
    where its command substitutions start, where a quote left open began, where the
    last whole line ends), from one pass, so all agree on what is
    quoted. The line is read the way Bash reads quoting: single and double quotes, the backslash, ANSI-C
    quoting, a comment from an unquoted `#` that begins a word to the line end (a
    backslash before that line end does not continue it), and the body of a
    here-document. What is in a comment or a body is still split into words: cmd.exe
    runs the words after a `#`, and a body can be a script handed to a shell."""
    out, quote, i, size = [], "", 0, len(command)
    quote_start, unfinished, completed = None, None, 0
    word_start = True       # where an unquoted # begins a comment
    opened: list = []       # the here-documents opened on this line: (delimiter, leading tabs dropped)
    starts: list = []       # (index, "$(" or "`") of each substitution the shell would run
    in_backticks = False
    expansion = 0           # where the `${...}` being read ends: no comment or here-document begins inside one
    joins: list = []        # for each open parenthesis: the word it stands in goes on after it closes
    cases: list = []        # for each open `case`: how many parentheses were open where it began
    while i < size:
        c = command[i]
        after = _after_continuations(command, i + 1)
        follower = command[after:after + 1]
        if c == "\n" and not quote:
            completed = i
        if "\ue000" <= c <= "\uf8ff":
            out.append(_literal(c))
            i += 1
            word_start = False
            continue
        if quote == "'":
            quote = "" if c == "'" else quote
            out.append(_PLAIN.get(c, c))
            word_start = False
        elif c == "\\" and i + 1 < size:
            i += 1
            if command[i] == "\n":
                # A continuation. Not so before CR LF: there the backslash makes the CR an
                # ordinary character, and the LF still ends the command.
                i += 1
                continue
            out.append(c + _PLAIN.get(command[i], _literal(command[i])))
            word_start = False
        elif quote:
            word_start = False
            if c == "$" and follower == "{":
                # "${x:-"'"}": the word in the braces has quoting of its own, and the
                # expansion is part of this one word.
                stop = _parameter_end(command, i, True)
                if stop is None or _QUOTING & set(command[i + 2:stop - 1]):
                    starts.append((i, command[i:after + 1] + '"'))
                    if stop is not None:
                        starts += [(i + 2 + found.start(), found.group())
                                   for found in _BODY_SUBSTITUTION.finditer(command[i + 2:stop - 1])]
                        out.append(_FILLED["$"] + _sealed(command[i + 1:stop]))
                        i = stop
                        continue
            if c == "$" and follower == "(":
                # "$(printf "x;echo ")": the substitution has quotes of its own, and its
                # output is part of this one word.
                starts.append((i, command[i:after + 1]))
                stop = _substitution_end(command, i)
                if stop is not None:
                    out.append(_FILLED["$"] + _sealed(command[i + 1:stop]))
                    i = stop
                    continue
                # It does not close, so its words cannot be told: the `$` stays unmarked,
                # and the word counts as one the shell builds.
                out.append(c)
                i += 1
                continue
            if c == "`":
                starts.append((i, "`"))
                stop = i + 1
                while stop < size and command[stop] != "`":
                    stop += 2 if command[stop] == "\\" else 1
                if stop < size:
                    out.append(_FILLED["`"] + _sealed(command[i + 1:stop]) + _FILLED["`"])
                    i = stop + 1
                    continue
            quote = "" if c == '"' else quote
            out.append((_FILLED.get(c) if c in "$`" else None) or _PLAIN.get(c, c))
        else:
            if _join and c == "\r":
                # Unix Bash keeps a CR inside an unquoted word.
                out.append(_PLAIN[c])
                word_start = False
                i += 1
                continue
            if c == "#" and word_start and i >= expansion:
                stop = command.find("\n", i)
                stop = size if stop < 0 else stop
                out.append(_inert(command[i:stop]))
                i = stop
                continue
            if word_start and c in "ce" and _case_word(command, i):
                # the `)` that ends a `case` pattern closes nothing
                if c == "c":
                    cases.append(len(joins))
                elif cases:
                    cases.pop()
            if c == "\n" and opened:
                out.append(c)
                i += 1
                for delimiter, tabs in opened:
                    body, joined, continued = i, "", False
                    while i < size:
                        stop = command.find("\n", i)
                        stop = size if stop < 0 else stop
                        line = command[i:stop]
                        out.append(_inert(line) + command[stop:stop + 1])
                        # The shell expands a substitution in a body (unless its delimiter is quoted).
                        starts += [(i + found.start(), found.group()) for found in _BODY_SUBSTITUTION.finditer(line)]
                        i = min(stop + 1, size)
                        if _tails is not None and not getattr(delimiter, "quoted", True):
                            # Bash joins escaped line ends before testing an unquoted delimiter.
                            joined += line
                            if (len(line) - len(line.rstrip("\\"))) % 2 and stop < size:
                                joined, continued = joined[:-1], True
                            else:
                                if continued and (joined.lstrip("\t") if tabs else joined).rstrip("\r") == delimiter:
                                    _tails.append(command[i:])
                                joined, continued = "", False
                        if (line.lstrip("\t") if tabs else line).rstrip("\r") == delimiter:
                            break
                    starts += [(body + found.start(), found.group())
                               for found in _BODY_SUBSTITUTION.finditer(command[body:i]) if "\\\n" in found.group()]
                opened, word_start = [], True
                continue
            if c == "<" and i >= expansion:
                found = _heredoc(command, i)
                if found:
                    opened.append(found)
            if c == "$" and follower == "{":
                # ${x:-"}"} or ${x:-'}'}: the same outside double quotes. The `$` stays
                # unmarked: the shell can turn this word into several.
                stop = _parameter_end(command, i, False)
                if stop is not None:
                    # A `#` or `<<` inside the braces is part of the expansion's word
                    # (`${x:-(#}`, `${x#y}`): no comment and no here-document begins there.
                    expansion = max(expansion, stop)
                if stop is None or _QUOTING & set(command[i + 2:stop - 1]):
                    starts.append((i, command[i:after + 1]))
                    if stop is not None:
                        starts += [(i + 2 + found.start(), found.group())
                                   for found in _BODY_SUBSTITUTION.finditer(command[i + 2:stop - 1])]
                        out.append('$"' + _sealed(command[i + 1:stop]) + '"')
                        i, word_start = stop, False
                        continue
            if c in "$<>" and follower == "(":
                starts.append((i, command[i:after + 1]))
                if _join:
                    # A substitution belongs to the outer command. Keep the old reading
                    # too, so its body and ordinary local commands still have their routes.
                    stop = _substitution_end(command, i)
                    if stop is not None:
                        out.append(("$" if c == "$" else _FILLED[c]) + '"' + _sealed(command[after:stop]) + '"')
                        i, word_start = stop, False
                        continue
            if word_start and c == "{":
                stop = _descriptor_end(command, i)
                if stop is not None:
                    # A named descriptor contributes no argument, but its index can run code.
                    starts += [(i + at, mark) for at, mark in _substitution_starts(command[i:stop])]
                    i = stop
                    continue
            if word_start and c in "0123456789" and _FILE_DESCRIPTOR.match(command, i):
                # A number directly before a redirection is a file descriptor (`2>&1`) and not
                # a word of the command. With a space between, it is a word: `--target-org 123
                # >&2` names the org 123.
                i = _FILE_DESCRIPTOR.match(command, i).end()
                continue
            if c == "`":
                if not in_backticks:
                    starts.append((i, "`"))
                    if _join:
                        stop = _closing(command, i + 1, "`")
                        if stop is not None:
                            out.append('"`' + _sealed(command[i + 1:stop - 1]) + '`"')
                            i, word_start = stop, False
                            continue
                in_backticks = not in_backticks
            if c == "$" and follower == "'":
                # $'...' outside quotes is ANSI-C quoting: its escapes are decoded and the
                # result is plain text. Inside double quotes the same characters are not.
                stop = after + 1
                while stop < size and command[stop] != "'":
                    stop += 2 if command[stop] == "\\" else 1
                if stop < size:
                    out.append(_plain_word(_ansi_c(command[after + 1:stop])))
                    i = stop + 1
                    word_start = False
                    continue
                unfinished = i
            if c == "$" and follower == '"':
                # $"..." is a string the shell may translate (locale quoting): with a message
                # catalogue in place its text is another. The `$` stays, so the word counts
                # as one the shell builds.
                out.append(c)
                i += 1
                word_start = False
                continue
            quote = c if c in "'\"" else ""
            if quote:
                quote_start = i
            out.append(c)
            word_start = c in _WORD_BREAK
            if i < expansion:
                pass                    # a parenthesis inside `${...}` is a character of its word
            elif c == "(":
                before = _before_continuations(command, i)
                joins.append(before in _JOINING or (before == "(" and bool(joins) and joins[-1]))
            elif c == ")" and not (cases and cases[-1] == len(joins)) and joins and joins.pop():
                word_start = False      # it closed a substitution or an array: the word goes on (_JOINING)
        i += 1
    return ("".join(out), starts,
            unfinished if unfinished is not None else quote_start if quote else None, completed)


_QUOTED = "\ue300"


def _continued_heredoc_tails(command: str) -> list[str]:
    tails: list[str] = []
    _scan(command, _tails=tails)
    return tails


class _Word(str):
    """A shell word that remembers how it was written: `raw` is the word with its
    plain and filled-in characters still marked (_mask), so a `$`, a backtick, a
    brace or a glob character left in `raw` stood outside quotes. `paren` is set
    when an opening parenthesis follows the word directly: in PowerShell that is
    an argument computed at run time (`torque (Get-Content words.txt)`)."""
    paren = False
    function = False

    def __new__(cls, raw: str):
        quoted, assignment = _QUOTED + "Q" in raw, bool(_ASSIGN_RE.match(raw))
        written = raw
        raw = raw.replace(_QUOTED + "Q", "").replace(_QUOTED + "L", _QUOTED)
        text = raw.translate(_UNMASK)
        text = _LITERAL_MARK.sub(lambda found: chr(int(found.group(1), 16)), text)
        text = text.encode("utf-8", "surrogateescape").decode("utf-8", "surrogateescape")
        word = super().__new__(cls, text)
        word.raw, word.written = raw, written
        word.quoted, word.assignment = quoted, assignment
        return word


def _raw(tok: str) -> str:
    """The word as written. A word whose writing is not known (the command's quotes
    did not pair, or the gate put the word together) is read as unquoted."""
    return getattr(tok, "raw", tok)


def _is_keyword(tok: str) -> bool:
    return tok in _KEYWORDS and not getattr(tok, "quoted", False)


def _is_assignment(tok: str) -> bool:
    return getattr(tok, "assignment", bool(_ASSIGN_RE.match(tok)))


def _filled(tok: str) -> bool:
    """A variable or a command substitution inside double quotes fills in this word."""
    return bool(_FILLED_MARKS & set(_raw(tok)))


def _adds_words(words: list[str]) -> bool:
    """The shell can turn one of these words into more words, or into an option, after
    the gate has read the command: an unquoted variable, command substitution or
    brace expansion, a glob that begins a word or sits in an option, or an argument
    PowerShell computes, splats (`@MORE`) or passes on unread (`--%`). A glob inside a word (`ApexClass:Acct*`, `force-app/*`) can
    only become file names that begin the same way. A double-quoted variable is one
    word, but one that begins with it can be a whole option (`"$X"` holding
    `--target-org=prod`), so it counts unless it follows an option, where it is that
    option's value. The gate does not know which of sf's options take a value, so
    after one that takes none (`--json "$X"`) it is still read as a value."""
    for i, tok in enumerate(words):
        raw = _raw(tok)
        if _EXPANSION_CHARS & set(raw) or raw[:1] in _GLOB_CHARS or (raw[:1] == "-" and _GLOB_CHARS & set(raw)):
            return True
        if raw == "--%" or (raw[:1] == "@" and len(raw) > 1):
            # PowerShell: `@MORE` passes the items of $MORE as words of their own, and
            # what follows `--%` reaches the program as it stands (`--% -o prod`).
            return True
        if raw[:1] in _FILLED_MARKS and not (i and words[i - 1].startswith("-")):
            return True
    return bool(words) and bool(getattr(words[-1], "paren", False))


_BRACE_SEQUENCE = re.compile(r"([-+]?[0-9]+)\.\.([-+]?[0-9]+)(?:\.\.([-+]?[0-9]+))?\Z"
                             r"|([A-Za-z])\.\.([A-Za-z])(?:\.\.([-+]?[0-9]+))?\Z")
_BRACE_LIMIT = 64


def _brace_sequence(found) -> list[str] | None:
    """The items of `{1..5}`, `{05..01..2}` or `{a..e}`, or None when invalid or too many."""
    numbers = found.group(1) is not None

    def integer(text: str) -> int:
        return int(re.sub(r"\A([-+]?)0+(?=[0-9])", r"\1", text))

    try:
        first, last = (integer(found.group(1)), integer(found.group(2))) if numbers else (
            ord(found.group(4)), ord(found.group(5)))
        step = integer(found.group(3 if numbers else 6) or "1")
    except ValueError:
        return None
    # Bash leaves an overflowing integer sequence as written, including its braces.
    if any(not -(1 << 63) <= item < (1 << 63) for item in (first, last, step)) or (
            first > 0 and last < -(1 << 63) + 3 + first or
            first < 0 and last > (1 << 63) - 3 + first) or (step == -(1 << 63) and first < last):
        return None
    step = abs(step) or 1
    if abs(last - first) // step >= _BRACE_LIMIT:
        return None
    items = list(range(first, last + (1 if last >= first else -1), step if last >= first else -step))
    if not numbers:
        return [chr(item) for item in items]
    ends = (found.group(1), found.group(2))
    width = max(map(len, ends)) if any(len(text) > 1 and text.startswith("0") or
                                     len(text) > 2 and text.startswith("-0") for text in ends) else 0
    return [str(item).zfill(width) for item in items]


def _brace_expand(raw: str) -> list[str]:
    """The words Bash makes of one word by brace expansion (`{a,b}c`, `{1..3}`, one inside
    another), from the word as written: a brace inside quotes is marked in `raw` and is
    none. Brace expansion needs nothing but the text of the line, so what it makes is
    read as the command it is: `{sf,data,query,-o,B}` runs `sf data query -o B`. More
    than _BRACE_LIMIT words are not worked out, and the word is left as it stands."""
    start = raw.find("{")
    while start >= 0:
        level, commas, end = 0, [], -1
        for at in range(start, len(raw)):
            if raw[at] == "{":
                level += 1
            elif raw[at] == "}":
                level -= 1
                if not level:
                    end = at
                    break
            elif raw[at] == "," and level == 1:
                commas.append(at)
        if end > 0 and raw[start - 1:start] != "$":         # `${...}` is an expansion, not this
            cuts = [start, *commas, end]
            sequence = _BRACE_SEQUENCE.match(raw[start + 1:end])
            parts = [raw[a + 1:b] for a, b in zip(cuts, cuts[1:])] if commas else \
                _brace_sequence(sequence) if sequence else None
            if parts is not None:
                try:
                    tails, out = _brace_expand(raw[end + 1:]), []
                    for part in parts:
                        for piece in _brace_expand(part):
                            for tail in tails:
                                out.append(raw[:start] + piece + tail)
                                if len(out) > _BRACE_LIMIT:
                                    return [raw]
                except RecursionError:
                    return [raw]
                return out
        start = raw.find("{", start + 1)
    return [raw]


def _tokens(command: str, _join: bool = False) -> list[str] | None:
    """Quote-aware words and operator tokens, or None when the quoting does not parse.
    Each is a _Word: a str that remembers how it was written."""
    # Quoting any part of a word prevents reserved-word recognition; quoting a
    # name or its equals sign prevents assignment recognition. Keep that fact
    # before shlex removes the quotes and backslashes, also for empty quotes.
    text, out, quote, i = _mask(command, _join).replace(_QUOTED, _QUOTED + "L"), [], "", 0
    while i < len(text):
        c = text[i]
        if c == "\\" and quote != "'":
            out.append(_QUOTED + "Q" + text[i:i + 2])
            i += 2
            continue
        if c in "'\"" and (not quote or c == quote):
            if not quote:
                out.append(_QUOTED + "Q")
            quote = "" if quote else c
        out.append(c)
        i += 1
    lex = shlex.shlex("".join(out), posix=True, punctuation_chars=_PUNCTUATION)
    lex.whitespace_split = True
    lex.whitespace = " \t\r"
    lex.commenters = ""
    try:
        return [_Word(raw) for raw in lex]
    except ValueError:
        return None


def _is_operator(tok: str) -> bool:
    """An operator the shell acts on: written outside quotes (a quoted `>` or `;` is a word)."""
    raw = _raw(tok)
    return bool(raw) and all(c in _PUNCTUATION for c in raw)


def _is_redirect(tok: str) -> bool:
    return _is_operator(tok) and ("<" in tok or ">" in tok) and (
        tok == ">|" or not (set(tok) & _SEPARATOR_CHARS))


def _split(command: str, _join: bool = False) -> tuple[list[list[str]], list[str]] | None:
    """(segments of command words without redirections, operator tokens seen)."""
    toks = _tokens(command, _join)
    if toks is None:
        return None
    segments: list[list[str]] = [[]]
    operators: list[str] = []
    skip, backticks = False, 0
    for at, tok in enumerate(toks):
        if skip:
            skip = False
            if not _is_operator(tok):
                continue
        if _is_operator(tok):
            operators.append(tok)
            if segments[-1] and (tok.startswith("(") or (tok.startswith("`") and backticks % 2 == 0)):
                # WORD (...) or WORD `...`: an argument computed at run time follows the word.
                segments[-1][-1].paren = True
                end, parens = at + 1, str(tok)
                if parens.rstrip("\n") == "(" and end < len(toks) and _is_operator(toks[end]):
                    parens += toks[end]
                    end += 1
                if parens.replace("\n", "").startswith("()(") or (
                        parens.replace("\n", "") == "()" and end < len(toks) and _raw(toks[end]) in ("{", "(")):
                    # A function's name defines what later calls run; its body is read separately.
                    segments[-1][-1].function = True
            backticks += tok.count("`")
            if _is_redirect(tok):
                skip = True         # a file descriptor before it (`2>&1`) is not among the words: _scan
            else:
                segments.append([])
            continue
        segments[-1].append(tok)
    return [s for s in segments if s], operators


def is_simple(command: str, tool_name: str = "Bash") -> bool:
    """One command: no chaining, pipes, redirection, background, substitution or newline.
    A PowerShell call is one command only when every reading of it is (_powershell_texts):
    `sf a -v $'x\\' ; sf b #'` is one command for Bash and two for PowerShell. One call
    operator at its start is not a chain: it is how PowerShell runs a quoted path
    (`& "C:/Program Files/Torque/torque.exe" guarded counts ...`)."""
    if "powershell" in tool_name.casefold():
        plain, readings, more = _powershell_texts(command)
        return not more and all(_one_command(re.sub(r"\A\s*&\s*(?=\S)", "", text)) for text in (command, plain, *readings))
    return _one_command(command)


def _one_command(command: str) -> bool:
    # A line continued with a backslash is one line, and so is one with a line end inside
    # quotes or at its very end; _mask drops the first and marks the second.
    command = command.strip()
    if "`" in command or "$(" in command or "<(" in command or ">(" in command or "\n" in _mask(command):
        return False
    parsed = _split(command)
    return parsed is not None and len(parsed[0]) == 1 and not parsed[1]


def _topic(rest: list[str]) -> tuple[str, ...]:
    words = []
    for tok in rest:
        if tok.startswith("-"):
            break
        words.append(tok)
    return tuple(words)


def _match(topic: tuple[str, ...], table) -> bool:
    return any(topic[:len(key)] == key for key in table)


def _sf_org_flags(topic: tuple[str, ...]) -> set[str]:
    """-v names the Dev Hub only for package and org creation commands; elsewhere
    (data update record -v "Name=A") it carries values."""
    if topic[:1] in (("package",), ("package1",), ("org",)):
        return set(g.ORG_FLAGS)
    return set(g.ORG_FLAGS) - {"-v"}


def _container_mode() -> bool:
    """The Salesforce CLI is in container mode in this environment (any value but
    false or 0, to fail closed)."""
    return any(os.environ.get(name, "").strip().casefold() not in ("", "false", "0") for name in SF_CONTAINER_VARS)


def _prints_login_url(rest: list[str]) -> bool:
    """`sf org open` prints its login URL instead of only opening a browser: in
    container mode, with --url-only (-r, --urlonly) or --json, with --flags-dir
    (whose files can set either), or with a word the shell could turn into one of
    them: a variable or brace expansion, or a glob that can match a file named
    like an option."""
    if _container_mode():
        return True
    for tok in rest:
        if tok.split("=", 1)[0] in SF_OPEN_URL_FLAGS or _EXPANSION_CHARS & set(tok):
            return True
        if tok[:1] in ("-", *_GLOB_CHARS) and _GLOB_CHARS & set(tok):
            return True
        if tok.startswith("-") and not tok.startswith("--"):
            for letter in tok[1:]:
                if letter == "r":
                    return True
                if letter in SF_OPEN_VALUE_LETTERS:
                    break
    return False


def _sf_refused(topic: tuple[str, ...], rest: list[str], anywhere: bool = False) -> tuple[str, str] | None:
    """("all_orgs" or "credential", note) when an sf command lists every org or prints
    or takes a credential, else None. The command's words are read as the CLI reads
    them: in any order (`sf display org`), in the colon spelling (`org:display`),
    without the legacy `force` prefix (`force:org:display`), and shortened (`sf
    display`, which the CLI completes; the note then names the full command). With
    `anywhere` the words are not the leading ones (flag values sit among them), so a
    command's words count wherever they are."""
    words = tuple(part for word in topic for part in re.split(r"[:\s]+", word.casefold())
                  if part and part != "force")
    if words[:1] and words[0] in SF_DESCRIBE_ONLY:
        return None

    def named(key: tuple[str, ...]) -> bool:
        return set(key) <= set(words if anywhere else words[:len(key) + 1])
    if named(("org", "list")) and not set(words[:4]) & SF_ORG_LIST_ONE_ORG:
        return "all_orgs", ""
    if any(named(key) for key in SF_ALL_ORGS - {("org", "list")}):
        return "all_orgs", ""
    if any(named(key) for key in SF_CREDENTIALS):
        return "credential", ""
    if named(("api", "request", "rest")):
        headers = [value for _, value in _rest_headers(rest)]
        if any(header.partition(":")[0].casefold() == "authorization" for header in headers):
            return "credential", ""
    given = set(words)
    if (named(("org", "open")) or ("open" in given and given <= SF_OPEN_WORDS)) and _prints_login_url(rest):
        return "credential", ""
    for kind, name in SF_REFUSED_NAMES:
        if given and given <= set(name):
            return kind, f" (the CLI can complete this to sf {' '.join(name)})"
    return None


def _attached_short(rest: list[str]) -> bool:
    """A short option with something attached: -XDELETE, -oALIAS, -o=ALIAS, or short
    options written together (-to ALIAS). The CLI reads an option and a value
    there (and the last org named wins); the gate would read one unknown word."""
    return any(re.fullmatch(r"-[A-Za-z].+", tok) for tok in rest)


def _sf_read_words(rest: list[str]) -> list[str]:
    """Complete read names in the CLI's colon spelling or any word order."""
    names = sorted((SF_READ - SF_RECORD_READS - SF_LOG_READS) | SF_CHECK_ONLY
                   | {("api", "request", "rest")},
                   key=len, reverse=True)
    for name in names:
        for end in range(1, min(len(name), len(rest)) + 1):
            words = [part for word in rest[:end] for part in word.casefold().split(":")]
            if len(words) == len(name) and set(words) == set(name):
                return [*name, *rest[end:]]
    return rest


# The options with which `sf api request rest` can only be a GET, and whether each
# takes a value. Any other option (a body, a file, a flags folder, one this version
# does not know) makes the request a write.
REST_READ_OPTIONS = {"-o": True, "--target-org": True, "-X": True, "--method": True, "-H": True, "--header": True,
                     "--api-version": True, "-S": True, "--stream-to-file": True, "-i": False, "--include": False,
                     "--json": False}


def _rest_headers(rest: list[str]) -> list[tuple[int, str]]:
    """The header option takes every following value until another value option."""
    found, taking = [], False
    for index, word in enumerate(rest):
        if word.startswith("-"):
            name, attached, value = word.partition("=")
            if name in ("-H", "--header"):
                taking = True
                if attached:
                    found.append((index, value))
            elif REST_READ_OPTIONS.get(name) or name in ("-f", "--file", "-b", "--body", "--flags-dir"):
                taking = False
        elif taking:
            found.append((index, word))
    return found


def _rest_get(rest: list[str]) -> bool:
    i = 0
    while i < len(rest):
        if rest[i].startswith("-"):
            name, attached, value = rest[i].partition("=")
            takes = REST_READ_OPTIONS.get(name)
            if takes is None or (attached and not (takes and name.startswith("--"))):
                return False
            if takes and not attached:
                if i + 1 >= len(rest):
                    return False
                i += 1
                value = rest[i]
            if name in ("-X", "--method") and value.upper() != "GET":
                return False
        i += 1
    return True


def _rest_path(rest: list[str]) -> str | None:
    """The one URL of an `sf api request rest` line, wherever it stands among the
    options the gate knows (`-o ALIAS /services/...`), or None when there is not
    exactly one."""
    rest = _sf_read_words(rest)
    headers = {index for index, _ in _rest_headers(rest)}
    found, i = [], 0
    while i < len(rest):
        if rest[i].startswith("-"):
            name, attached, _ = rest[i].partition("=")
            i += 1 if attached or not REST_READ_OPTIONS.get(name) else 2
        elif i in headers:
            i += 1
        else:
            found.append(rest[i])
            i += 1
    return found[3] if found[:3] == ["api", "request", "rest"] and len(found) == 4 else None


def _rest_custom(path: str | None) -> bool:
    """Only the endpoint can run Apex; a query value, header or output name cannot."""
    import posixpath
    text = re.sub(r"(?i)^https?://[^/]+", "", _decoded(path or "").partition("?")[0]).replace("\\", "/")
    text = posixpath.normpath("/" + text.lstrip("/"))
    return bool(re.match(r"^/(services/)?apexrest(/|$)"
                         r"|^/(services/data/v\d+\.\d+/)?(tooling/)?executeanonymous/?$", text, re.IGNORECASE))


def _sf(rest: list[str], detail: str) -> Route:
    route = _sf_route(rest, detail)
    if route.kind in ("read", "check_only", "org_write", "no_org", "unverifiable") and _attached_short(rest):
        return Route("admin", route.org, detail + " (an option with its value attached, or short options written "
                                                  "together: write each option and its value as separate words)")
    return route


def _sf_route(rest: list[str], detail: str) -> Route:
    rest = _sf_read_words(rest)
    topic = _topic(rest)
    orgs = org_values(rest, _sf_org_flags(topic))
    org = orgs[0] if len(orgs) == 1 else None
    # A flag before the command words (sf --json org list): the CLI wants the command
    # first, but the gate does not rely on that. Every word after the flag is read,
    # and such a line is never local work.
    late = () if topic else tuple(tok for tok in rest if not tok.startswith("-"))
    refused = _sf_refused(topic or late, rest, anywhere=bool(late))
    if refused:
        return Route(refused[0], None, detail + refused[1])
    if any((_EXPANSION_CHARS | _GLOB_CHARS) & set(word) for word in (topic or late)[:3]):
        # sf org ${X:+display}, sf org d*: the shell picks the command at run time, so
        # the gate cannot tell which one runs and no approval covers it.
        return Route("admin", org, detail + " (its command words are built at run time)")
    if len(orgs) > 1:
        return Route("no_org", None, detail + " (names more than one org)")
    if late:
        return Route("org_write" if org else "no_org", org, detail)
    if not topic:
        return Route("local", None, detail) if not orgs else Route("org_write", org, detail)
    words = tuple(part for word in topic for part in word.casefold().split(":") if part and part != "force")
    for data, name in SF_READ_NAMES:
        if len(words) == len(name) and set(words) == set(name):
            if name == ("apex", "tail", "log"):
                if not any(flag in rest for flag in ("-s", "--skip-trace-flag", "--skiptraceflag")):
                    continue             # trace flag setup changes the org
            elif name in (("data", "query"), ("data", "soql", "query")):
                from urllib.parse import quote
                queries = _flag_values(rest, {"-q", "--query"})
                if queries and rest_data_class("/query?q=" + quote(queries[-1], safe="")) == "debug_logs":
                    data = "debug_logs"
            elif name == ("data", "get", "record"):
                objects = _flag_values(rest, {"-s", "--sobject", "--sobjecttype"})
                if objects and objects[-1].casefold() == "apexlog":
                    data = "debug_logs"
            # sf query data, sf data:query: the same read in another spelling.
            return Route("read" if org else "no_org", org, detail, data=data)
    if ":" in topic[0] and topic[0] in SFDX_READ:
        data = ("records" if topic[0] in SFDX_RECORD_READS else "debug_logs" if topic[0] in SFDX_LOG_READS
                else None)
        return Route("read" if org else "no_org", org, detail, data=data)
    if ":" in topic[0]:
        topic = ()      # a colon spelling the tables below do not hold: a write, unless it is incomplete
    if _match(topic, SF_ADMIN):
        return Route("admin", org, detail)
    if _match(topic, SF_ASK):
        return Route("unverifiable", org, detail)
    if _match(topic, SF_LOCAL) and not orgs and topic[:3] != ("cmdt", "generate", "fromorg"):
        if topic[0] == "plugins" and len(topic) > 1 and topic[1] != "inspect":
            return Route("unverifiable", None, detail)
        return Route("local", None, detail)
    if topic[:3] == ("api", "request", "rest"):
        # A custom Apex REST endpoint can change data on any method; a request file sets its own method.
        custom = _rest_custom(_rest_path(rest))
        # A path or a method the shell fills in ("/sobjects/Contact/describe$suffix") is
        # not the one the gate reads: it can be a row, a query, or a custom endpoint.
        # The `?` of an unquoted URL is left alone: as a glob it stands for one character,
        # so it can only become a path that is no endpoint.
        filled = any((_LOOSE_CHARS - {"?"}) & set(_raw(w)) or _filled(w) for w in rest)
        path = _rest_path(rest)
        kind = "read" if _rest_get(rest) and not custom and not filled and path is not None else "org_write"
        return Route(kind if org else "no_org", org, detail, data=rest_data_class(path) if kind == "read" else None)
    if topic[:3] == ("project", "deploy", "start") and "--dry-run" in rest:
        return Route("check_only" if org else "no_org", org, detail)
    if _match(topic, SF_CHECK_ONLY):
        return Route("check_only" if org else "no_org", org, detail)
    if _match(topic, SF_READ):
        data = "records" if _match(topic, SF_RECORD_READS) else "debug_logs" if _match(topic, SF_LOG_READS) else None
        return Route("read" if org else "no_org", org, detail, data=data)
    for _, name in SF_READ_NAMES:
        if set(words) < set(name):
            # sf query, sf log: with a terminal the CLI offers to complete it, and an
            # approval for it as a write would not ask for the read's consent.
            return Route("admin", org, detail + f" (an incomplete command name the CLI can complete to sf "
                                                f"{' '.join(name)}; write the full command)")
    return Route("org_write" if org else "no_org", org, detail)


# REST paths that return schema or org information, not record data. Anything
# else under the API (sobject rows, query, search, composite, UI API, Connect) is
# record data; Apex logs are debug logs. A Tooling row is record data too (it is
# read whole, author included); a Tooling query is schema only as far as
# _tooling_query_is_schema says.
# The whole path has to be one of these: a record can be fetched by an external id whose
# name or value is a word of this list (`/sobjects/Account/Describe__c/VALUE`,
# `/sobjects/Account/Key__c/describe`), and that is a record read.
_REST_METADATA = re.compile(
    r"^/?services/data/?(v[\d.]+/?)?$"
    r"|^/?(services/data/v[\d.]+/)?(tooling/)?(sobjects/?|describe/?|limits/?"
    r"|sobjects/[^/?]+/describe(/(layouts|compactLayouts|approvalLayouts|namedLayouts)(/[^/?]+)?)?/?)$",
    re.IGNORECASE)
# An external-id value can be named query, so only a whole query endpoint reads SOQL.
_REST_QUERY = re.compile(r"^/?(services/data/v\d+\.\d+/)?(query(All)?|tooling/query)/?$", re.IGNORECASE)
# Tooling API entities that hold schema, configuration or code. A query on any other
# entity (User, TraceFlag, ApexExecutionOverlayResult, test results) reads records.
TOOLING_SCHEMA_ENTITIES = frozenset(name.casefold() for name in (
    "ApexClass", "ApexTrigger", "ApexPage", "ApexComponent", "CustomField", "CustomObject", "CustomTab",
    "EntityDefinition", "FieldDefinition", "Flow", "FlowDefinition", "FlowDefinitionView", "Layout",
    "LightningComponentBundle", "PermissionSet", "Profile", "RecordType", "StaticResource", "ValidationRule",
    "WorkflowRule"))
# A field or relationship that names a person on those entities: who made or changed
# a component, who owns it, a user.
_TOOLING_PERSON = re.compile(
    r"(created|lastmodified)by(id)?$|^(owner|user|username|email)(id)?$"
    r"|^(businessowner|runninguser(field|entity)access|userlicense|usertype)(id)?$"
    r"|^permissions(chatterinternaluser|apiuseronly|(in|out)boundmigrationtoolsuser|portalsuperuser"
    r"|delegatedportaluseradmin|contenthubuser|insightsapp(upload)?user|lightningexperienceuser"
    r"|showcompanynameasuserbadge|iotuser|viewonlyembeddedappuser|lightningconsoleallowedforuser"
    r"|subscribereportsrunasuser|ltngpromoreserved01userperm)$", re.IGNORECASE)
_SOQL_STRING = re.compile(r"'(?:[^'\\]|\\.)*'")


def _tooling_query_is_schema(query: str) -> bool:
    """A Tooling query that returns schema only: one SELECT from one listed entity,
    with no field or relationship that names a person anywhere in it (a filter on
    such a field would answer questions about people too). Anything the gate
    cannot read this way is a record read."""
    text = _SOQL_STRING.sub("''", query)
    if "'" in text.replace("''", "") or "&" in text:
        return False
    match = re.fullmatch(r"\s*SELECT\s+(.+?)\s+FROM\s+([A-Za-z_][A-Za-z0-9_]*)\b(.*)", text,
                         re.IGNORECASE | re.DOTALL)
    if not match or match.group(2).casefold() not in TOOLING_SCHEMA_ENTITIES:
        return False
    rest = match.group(1) + " " + match.group(3)
    if re.search(r"\b(SELECT|TYPEOF)\b|\bFIELDS\s*\(", rest, re.IGNORECASE):
        return False
    for path in re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", rest):
        for part in path.split("."):
            words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", part).casefold().split()
            if _TOOLING_PERSON.search(part) or "user" in words or "owner" in words:
                return False
    return True


def _decoded(path: str) -> str:
    """The URL as the server reads it: percent escapes and + decoded, repeatedly."""
    import urllib.parse
    text = path or ""
    for _ in range(4):
        decoded = urllib.parse.unquote_plus(text)
        if decoded == text:
            break
        text = decoded
    return text


def rest_data_class(path: str) -> str | None:
    """The consent class a REST GET reads: debug_logs, None (schema) or records. The
    path is decoded first, so an escaped name is read as the server reads it."""
    full = _decoded(path)
    text, _, query = full.partition("?")
    if re.search(r"(^|/)\.{1,2}(/|$)", text) or "\\" in text or "#" in text or "#" in (path or ""):
        # .../sobjects/User/describe/../005...: the client folds the dots away before it
        # sends the request, so the server gets the row, not the describe.
        return "records"
    if _REST_METADATA.fullmatch(re.sub(r"(?i)^https?://[^/]+", "", text)):
        return None
    if re.match(r"^/?(services/data/v\d+\.\d+/)?(tooling/)?sobjects/apexlog(/|$)",
                re.sub(r"(?i)^https?://[^/]+", "", text), re.IGNORECASE):
        return "debug_logs"
    endpoint = _REST_QUERY.fullmatch(re.sub(r"(?i)^https?://[^/]+", "", text))
    tooling = endpoint and endpoint.group(2).casefold() == "tooling/query"
    if endpoint:
        # URL parameters are separated before their values are read as SOQL strings.
        if "&" in (path or "").partition("?")[2]:
            return "records"
        # The class comes from what the query reads, never from a word in its text
        # (`... WHERE LastName != 'ApexLog'` is a read of contacts).
        soql = re.fullmatch(r"q=(.*)", query, re.IGNORECASE | re.DOTALL)
        plain = _SOQL_STRING.sub("''", soql.group(1)) if soql else ""
        source = re.fullmatch(r"\s*SELECT\s+(.+?)\s+FROM\s+([A-Za-z_][A-Za-z0-9_]*)\b(.*)", plain,
                              re.IGNORECASE | re.DOTALL)
        if source and source.group(2).casefold() == "apexlog" and "'" not in plain.replace("''", "") \
                and "&" not in plain and not re.search(r"\bSELECT\b", source.group(1) + source.group(3),
                                                       re.IGNORECASE):
            return "debug_logs"
        return None if tooling and soql and _tooling_query_is_schema(soql.group(1)) else "records"
    return "records"


# The route owner for a command that names an internal initiative. Connected mode
# serves one client's work only, so an initiative is never the bound client.
INITIATIVE_OWNER = "initiative:"


def _rest_reads_logs(path: str | None) -> bool:
    """Keep log consent beside a cautious record class when the request can read logs."""
    import posixpath
    from urllib.parse import parse_qsl, quote
    text, _, query = (path or "").partition("#")[0].partition("?")
    text = re.sub(r"(?i)^https?://[^/]+", "", _decoded(text)).replace("\\", "/")
    text = posixpath.normpath("/" + text.lstrip("/"))
    if rest_data_class(text) == "debug_logs":
        return True
    return any(name.casefold() == "q"
               and rest_data_class(text + "?q=" + quote(value, safe="")) == "debug_logs"
               for name, value in parse_qsl(query, keep_blank_values=True))


def _strip_context(rest: list[str]) -> tuple[list[str], str | None]:
    out, client, initiative, i = [], None, None, 0
    while i < len(rest):
        tok = rest[i]
        name = tok.split("=", 1)[0]
        if name in ("--workspace", "--client", "--initiative"):
            value = tok.split("=", 1)[1] if "=" in tok else (rest[i + 1] if i + 1 < len(rest) else "")
            if name == "--client":
                client = value
            elif name == "--initiative":
                initiative = value
            i += 1 if "=" in tok else 2
            continue
        out.append(tok)
        i += 1
    return out, (INITIATIVE_OWNER + initiative if initiative is not None else client)


def _cut_short(rest: list[str]) -> str:
    """The first long option that is a prefix of one the gate decides by without being
    one of them (`--target`, `--wri`, `--cli=other`), or ''."""
    for tok in rest:
        name = tok.split("=", 1)[0]
        if name.startswith("--") and len(name) > 2 and name not in TORQUE_DECIDING_OPTIONS \
                and any(full.startswith(name) for full in TORQUE_DECIDING_OPTIONS):
            return name
    return ""


def _filled_decider(rest: list[str], names=None) -> str:
    """The first option the gate decides by whose value the shell fills in at run time
    (`--client "$NAME"`, `--initiative="$I"`, `--client $NAME`), or ''. A double-quoted
    variable is one word, so the command's shape is known, but not what the word says:
    a session bound to one client would read `--client "$acme"` as that client and run
    it for whatever the variable holds."""
    for i, tok in enumerate(rest):
        if tok.split("=", 1)[0] not in (TORQUE_VALUE_DECIDES if names is None else names):
            continue
        value = tok if "=" in tok or i + 1 == len(rest) else rest[i + 1]
        if _filled(value) or _LOOSE_CHARS & set(_raw(value)):
            return tok.split("=", 1)[0]
    return ""


def _built_at_run_time(rest: list[str], paren: bool = False) -> str:
    """Why the gate cannot tell which of Torque's own commands this line runs, or ''.
    `torque workspace ${x:-ai-access} full` runs `workspace ai-access`, and `torque
    workspace ai-acces* full` does too once a file of that name exists, while the
    gate would read some other command; `--summary=$x` becomes `--summary=a --write`
    when x holds a space. So no word may hold anything the shell expands outside
    quotes (a variable, a command substitution, a brace expansion, a glob), nor be
    PowerShell's `--%`, a splatted `@name` or a computed `(...)` argument. A
    double-quoted variable is one word whatever it holds, and is accepted where
    it can only be a value: attached to an option with `=`, or after an option
    that takes one (`--summary "$TEXT"`). Quoted text is plain text."""
    if paren:
        return " (an argument is computed at run time; write the command's words out)"
    for i, tok in enumerate(rest):
        raw = _raw(tok)
        if tok == "--%" or raw[:1] == "@" or _LOOSE_CHARS & set(raw):
            return " (the shell builds a word of it at run time; write the command's words out, or quote the text)"
        if _filled(tok):
            name = raw.split("=", 1)[0]
            attached = raw.startswith("--") and "=" in raw and not _FILLED_MARKS & set(name)
            if not attached and not (i and rest[i - 1] in TORQUE_VALUE_OPTIONS):
                return " (a word of it is filled in at run time where it could become an option; write it out)"
    return ""


def _guarded(rest: list[str], workspaces: list[str], detail: str, client: str | None) -> Route:
    """`torque guarded ...` after its context options are taken out (docs/guarded-reads.md).
    A lane names its org with --target-org and nothing else, as its own parser does."""
    # The command's own words. Words after a comment mark are not among them (in the
    # reading where they run at all, they are stray arguments); its options still count.
    words = tuple(_topic(rest[1:]))
    words = words[:next((i for i, word in enumerate(words) if word.startswith("#")), len(words))]
    names = {tok.split("=", 1)[0] for tok in rest if tok.startswith("-")}
    if names & {"-h", "--help"}:
        return Route("local", None, detail, client, workspace=(workspaces or ["."])[0])
    if names & (TORQUE_ORG_FLAGS - {"--target-org"}) or len(workspaces) > 1:
        return Route("admin", None, detail + " (a guarded read names its org with --target-org, and one "
                                             "workspace)", client)
    workspace = workspaces[0] if workspaces else None
    if words in GUARDED_LOCAL:
        return Route("local", None, detail, client, workspace=workspace or ".")
    if words in GUARDED_READS:
        orgs = org_values(rest, {"--target-org"})
        org = orgs[0] if len(orgs) == 1 else None
        return Route("read" if org else "no_org", org, detail, client, data=GUARDED_READS[words],
                     workspace=workspace or ".")
    return Route("admin", None, detail, client)


def _torque(rest: list[str], detail: str, paren: bool = False) -> Route:
    delegated = bool(rest) and rest[0] in TORQUE_DELEGATED
    if "--" in rest:
        # Torque's parser cuts a `-- COMMAND...` tail for these commands only
        # (cli_approval.split_tail); a delegated command's own parser reads the rest as
        # plain arguments. Anywhere else the words after `--` are still the command:
        # `torque -- workspace ai-access full` runs `workspace ai-access`.
        if delegated or rest[:2] in (["approval", "request"], ["approval", "require"]) or rest[:1] == ["launch"]:
            rest = rest[:rest.index("--")]
        else:
            return Route("admin", None, detail + " (this command takes no `--`; the words after it are still "
                                                 "read as the command)")
    if not delegated:
        note = _built_at_run_time(rest, paren)
        if note:
            # Refused like `sf org ${X:+display}`: the gate cannot tell which command runs.
            return Route("admin", None, detail + note)
    elif _attached_short(rest):
        return Route("admin", None, detail + " (an option with its value attached, or short options written "
                                             "together: write each option and its value as separate words)")
    short = _cut_short(rest)
    if short:
        return Route("admin", None, detail + f" (`{short}` is an option's name cut short, which a delegated "
                                             "command would still take as that option; write option names in full)")
    filled = _filled_decider(rest)
    if filled:
        return Route("admin", None, detail + f" (the shell fills in the value of `{filled}` at run time, and the "
                                             "gate decides by that value; write it out)")
    workspaces = _flag_values(rest, {"--workspace"})
    written = rest
    rest, client = _strip_context(rest)
    head = rest[0] if rest else ""
    sub = rest[1] if len(rest) > 1 else ""
    if head == "guarded":
        if _filled_decider(written, {"--workspace"}) or any(_SHELL_TILDE.match(value) for value in workspaces):
            # The gate compares the path as written with the workspace the session works in:
            # `"$PWD/.."` and `~+/..` read as this folder and run in its parent.
            return Route("admin", None, detail + " (a guarded read names its workspace with a path written out: "
                                                 "no variable, and no `~` other than `~/`)", client)
        return _guarded(rest, workspaces, detail, client)
    orgs = org_values(rest, TORQUE_ORG_FLAGS)
    org = orgs[0] if len(orgs) == 1 else None
    if len(orgs) > 1:
        return Route("no_org", None, detail + " (names more than one org)", client)
    if "" in orgs:
        # `--target-org '' other`: no org is named, and Windows PowerShell does not hand an
        # empty argument on at all, so the command would take the next word as its org.
        return Route("no_org", None, detail + " (an org option with an empty value)", client)
    if (head, sub) in (("client", "add"), ("change", "create")):
        # These org options record a planned target without connecting to it.
        return Route("local", None, detail, client)
    if (head, sub) == ("change", "verify-deploy"):
        return Route("read" if org else "no_org", org, detail, client)
    if head == "launch" or (head == "workspace" and sub in ("ai-access", "delegate", "guarded-reads")):
        return Route("admin", None, detail, client)
    if head == "approval":
        if sub in ("grant", "deny", "launch-binding") or (sub == "permissions" and "--write" in rest):
            return Route("admin", None, detail, client)
        names = {t.split("=", 1)[0] for t in rest}
        if sub == "request" and names & {"--capture-before-record", "--capture-before-metadata", "--capture-before"}:
            # Capturing a before-state reads the org now, in either spelling.
            records = bool(names & {"--capture-before-record", "--record"})
            return Route("read" if org else "no_org", org, detail, client, data="records" if records else None)
        return Route("local", None, detail, client)
    if (head == "client" and sub == "list") or (head == "engagement" and sub == "list"):
        # Lists every client's name and org: one client per session, so refused.
        return Route("local", None, detail, "*")
    if head == "initiative":
        # Initiatives are internal work; connected mode never serves them.
        return Route("local", None, detail, INITIATIVE_OWNER + (rest[2] if len(rest) > 2 else "*"))
    if head == "client" and sub == "consent":
        third = rest[2] if len(rest) > 2 else ""
        return Route("local" if third == "show" else "admin", None, detail, client)
    if head == "revert" and sub == "revert-token":
        return Route("admin", org, detail, client)
    if head in TORQUE_WRITE_ROUTES:
        if head == "recover" and sub in REVERT_READ:
            return Route("read" if org else "local", org, detail, client)
        if head == "revert" and sub in REVERT_READ:
            return Route("read" if org else "local", org, detail, client)
        if head == "revert" and sub == "revert" and len(rest) > 2 and rest[2] in REVERT_READ:
            return Route("read" if org else "local", org, detail, client)
        if not sub or sub.startswith("-") and sub in ("-h", "--help", "--version"):
            return Route("local", None, detail, client)
        if "--dry-run" in rest:
            return Route("check_only" if org else "no_org", org, detail, client)
        return Route("org_write" if org else "no_org", org, detail, client)
    if head in TORQUE_READ_ROUTES:
        data = "debug_logs" if head == "logs" else None
        if head == "advisory" and any(t.split("=", 1)[0] == "--where" for t in rest):
            # `advisory impact --where "Email = '...'"` counts the records any filter
            # selects. A count of one answers a question about a record, so this is a
            # record read; without a filter it is the object's size, which `metadata` covers.
            data = "records"
        return Route("read" if org else "local", org, detail, client, data=data)
    if head in TORQUE_BROWSER_ROUTES:
        headed = any(len(n) >= 4 and "--headed".startswith(n) for n in (t.split("=", 1)[0] for t in rest))
        return Route("browser_write" if org else "local", org, detail, client, headed=headed)
    if head in TORQUE_WRITE_ELSE:
        return Route("org_write" if org else "local", org, detail, client)
    if org:
        return Route("org_write", org, detail, client)
    return Route("local", None, detail, client)


def _inner_start(words: list[str]) -> int | None:
    """Index of the first word (after the head) that starts a recognized org-capable
    command: sf/sfdx, a Torque console script, or a Python launcher running torque."""
    start, end, exact = 1, len(words), False
    head = g._basename(words[0]) if words else ""
    if head in ("xargs", "sudo"):
        unknown, options = False, []
        while start < len(words):
            took, unknown = _wrapper_options(head, words[start:], options)
            start += took
            if unknown or head != "sudo" or start >= len(words) or (took and words[start - 1] == "--") or \
                    "=" not in words[start] or words[start].startswith(("/", "=")):
                break
            start += 1
        stopped = ({"-e", "-h", "-l", "-v", "-V", "-K", "--edit", "--help", "--list", "--validate",
                    "--version", "--remove-timestamp"} if head == "sudo" else {"--help", "--version"})
        if not unknown and stopped & set(options):
            return None
        if not unknown and (start >= len(words) or not (_filled(words[start]) or
                _LOOSE_CHARS & set(_raw(words[start])))):
            # A known runner's later words are its command's data. Unknown options
            # and commands built at run time keep the loose reading.
            end, exact = min(start + 1, len(words)), True
        else:
            start = 1
    for i in range(start, end):
        word = words[i]
        base = g._basename(word)
        if g._is_sf_token(word) or base in g.CONSOLE_SCRIPTS or base in SHELLS or base == "env":
            return i
        if g.PY_LAUNCHER_RE.match(base) or (exact and base in BENIGN_WRAPPERS | {"xargs", "sudo", "find"}):
            return i
    return None


# The options of the wrapper commands: short ones that take no value, short ones that take
# the next word (or the rest of their own word) as their value, and the same for the long
# ones. `time -o FILE command` runs the command, and FILE can be any word, `echo` among
# them. An option in none of these lists is one the gate does not know: it could take the
# next word as its value too, so the command is asked about.
_WRAPPERS = {
    "command": ("pvV", "", (), ()), "builtin": ("", "", (), ()), "exec": ("cl", "a", (), ()),
    "nohup": ("", "", (), ()), "noglob": ("", "", (), ()), "nocorrect": ("", "", (), ()),
    "time": ("pavqlh", "of", ("--append", "--verbose", "--quiet", "--portability"), ("--output", "--format")),
    "nice": ("", "n", (), ("--adjustment",)), "stdbuf": ("", "ioe", (), ("--input", "--output", "--error")),
    "timeout": ("v", "sk", ("--preserve-status", "--foreground", "--verbose"), ("--signal", "--kill-after")),
    "caffeinate": ("dimsu", "tw", (), ()),
    "xargs": ("0eiloprtx", "adEIJLnPRSs",
              ("--null", "--eof", "--replace", "--max-lines", "--open-tty", "--interactive",
               "--no-run-if-empty", "--show-limits", "--verbose", "--exit", "--help", "--version"),
              ("--arg-file", "--delimiter", "--max-args", "--max-chars", "--max-procs", "--process-slot-var")),
    "sudo": ("ABbEeHiKklnNPSsVv", "aCcDgpRrTtUu",
             ("--askpass", "--bell", "--background", "--preserve-env", "--edit", "--set-home", "--login",
              "--remove-timestamp", "--list", "--preserve-groups", "--shell", "--validate", "--help",
              "--reset-timestamp", "--no-update", "--non-interactive", "--stdin", "--version"),
             ("--auth-type", "--close-from", "--login-class", "--chdir", "--group", "--host", "--prompt",
              "--chroot", "--role", "--command-timeout", "--type", "--other-user", "--user")),
}


# env's options: those that take no value, those that take the next word (`-a NAME` is the
# name the command is started under, `-P PATH` where BSD env looks for it), and the long
# ones written with `=`.
_ENV_FLAGS = ("-i", "-", "-0", "-v", "--ignore-environment", "--null", "--debug", "--list-signal-handling")
_ENV_VALUES = ("-u", "--unset", "-C", "--chdir", "-a", "--argv0", "-P")
_ENV_ATTACHED = ("--unset", "--chdir", "--argv0", "--block-signal", "--default-signal", "--ignore-signal")


def _wrapper_options(head: str, rest: list[str], options: list[str] | None = None) -> tuple[int, bool]:
    """(how many of the words after a wrapper command are its options and their values,
    whether one of them is an option the gate does not know)."""
    flags, values, long_flags, long_values = _WRAPPERS.get(head, ("", "", (), ()))
    if head in ("command", "builtin", "exec", "nohup", "nice", "stdbuf", "timeout", "time"):
        long_flags += ("--help",)
    if head in ("nohup", "nice", "stdbuf", "timeout", "time"):
        long_flags += ("--version",)
    i, unknown = 0, False
    while i < len(rest) and rest[i].startswith("-") and len(rest[i]) > 1:
        word = str(rest[i])
        if word == "--":
            i += 1
            break
        if word.startswith("--"):
            name = word.split("=", 1)[0]
            if head in ("xargs", "sudo") and name not in long_flags + long_values:
                matches = [option for option in long_flags + long_values if option.startswith(name)]
                if len(matches) == 1:
                    name = matches[0]
            unknown = unknown or name not in long_flags + long_values
            if options is not None and name in long_flags + long_values:
                options.append(name)
            i += 2 if name in long_values and "=" not in word else 1
            continue
        took = 1
        for at, letter in enumerate(word[1:]):
            if head == "xargs" and letter in "eil":
                break                   # These optional values only take text in the same word.
            if head == "sudo" and letter == "h":
                took = 2 if word == "-h" and i + 1 < len(rest) and \
                    not rest[i + 1].startswith("-") and "=" not in rest[i + 1] else 1
                if options is not None and took == 1 and at + 2 == len(word):
                    options.append("-h")
                break                   # An attached host, or one after -h, is its optional value.
            if letter in values:
                # the rest of the word is its value (`-oL`), or the next word is (`-o L`)
                took = 1 if at + 2 < len(word) else 2
                break
            if options is not None and letter in flags:
                options.append("-" + letter)
            if letter not in flags and not (head == "nice" and word[1:].isdigit()):
                unknown = True
                break
        i += took
    return i, unknown


def _strip_benign(head: str, words: list[str]) -> list[str]:
    rest = words[1:]
    rest = rest[_wrapper_options(head, rest)[0]:]
    if head == "timeout" and rest:
        rest = rest[1:]         # its duration
    return rest


def _segment(words: list[str], depth: int) -> list[Route]:
    """The routes of one simple command. An unquoted word that begins with `#` starts a
    comment in Bash and PowerShell: the words from there on do not run. cmd.exe has no
    such comment and runs them. The gate cannot always tell which shell reads the
    line, so both readings have to pass: `sf data query ... # -o dev` is read without
    its org (refused), and `sf project deploy start ... # --dry-run` is a write."""
    if words and _raw(words[0]).startswith("#"):
        return []           # a line that is only a comment: no shell runs anything there
    cut = next((i for i, word in enumerate(words) if i and _raw(word).startswith("#")), None)
    if cut is None:
        return _segment_words(words, depth)
    return list(dict.fromkeys(_segment_words(words[:cut], depth) + _segment_words(words, depth)))


def _segment_words(words: list[str], depth: int) -> list[Route]:
    had_assignment = False
    questions = []
    if words and _is_keyword(words[0]) and words[0] in ("for", "select", "case"):
        return [Route("local", None, " ".join(words[:5]))]
    while words and ((_is_keyword(words[0]) and not had_assignment) or _is_assignment(words[0])
                     or getattr(words[0], "function", False)):
        if words[0] == "time" and len(words) > 1 and words[1] == "-p":
            words = words[2:]             # the reserved word's portable option
            continue
        if words[0] == "coproc" and len(words) > 1 and (
                (len(words) > 2 and not getattr(words[2], "quoted", False)
                 and words[2] in ("{", "if", "while", "until", "for", "select", "case", "[["))
                or (len(words) == 2 and getattr(words[1], "paren", False))):
            words = words[2:]             # a name before a compound command names the coprocess
            if words and words[0] in ("for", "select", "case"):
                return questions + [Route("local", None, " ".join(words[:5]))]
            continue
        if getattr(words[0], "function", False) or (words[0] == "function" and len(words) > 1):
            name = words[0] if getattr(words[0], "function", False) else words[1]
            if name in {"sf", "sfdx", "torque", "jsc"} | LOCAL_COMMANDS | BENIGN_WRAPPERS:
                questions.append(Route("unverifiable", None, str(name) + " (defines what this command name runs)"))
            words = words[1:] if getattr(words[0], "function", False) else words[2:]
            continue
        had_assignment = had_assignment or _is_assignment(words[0])
        words = words[1:]
    if not words:
        return questions + ([] if not had_assignment else [Route("local", None, "")])
    if words[0] == "[[" and not had_assignment and not getattr(words[0], "quoted", False):
        return questions + [Route("local", None, " ".join(words[:5]))]
    routes = questions + _segment_core(words, depth)
    if had_assignment:
        # A variable set for one command (HOME, SF_*) can point an alias at another org.
        routes = _asked_as_well(routes, " (with a variable assignment)")
    return routes


def _asked_as_well(routes: list[Route], note: str) -> list[Route]:
    """The routes of a command the gate cannot fully check (a variable is set for it).
    A read keeps its route, so the consent's data classes still have to cover it,
    and gets an `unverifiable` route beside it: the consultant is asked. A write
    becomes `unverifiable` alone, as before: an approval was granted for the org
    the alias named without the variable, so it cannot stand for this call."""
    out = []
    for route in routes:
        if route.kind in ("read", "check_only"):
            out.append(route)
        if route.kind in ("read", "check_only", "org_write", "browser_write"):
            out.append(Route("unverifiable", route.org, route.detail + note, route.client))
        else:
            out.append(route)
    return out


_ADDS_WORDS_NOTE = " (the shell adds words to it at run time)"
_WINDOWS = os.name == "nt"
_CMD_NAME = re.compile(r"%([^%]+)%")


def _cmd_fills(words: list[str]) -> bool:
    """On Windows `sf` is often a `.cmd` launcher, and cmd.exe reads each argument again
    on its way to the program, from Bash as from PowerShell (checked with a program
    that records its arguments): it fills in `%NAME%`, and the value's own spaces,
    quotes and `&` then count (`--since %X%` with `-o other` in X arrives as three
    words); a double quote inside an argument ends cmd's quoting; and in a word
    without spaces `&`, `|`, `<`, `>` and `^` are cmd's own. A `%NAME%` inside a
    longer quoted argument (`LIKE '%acme%'`) stays inside it unless the variable's
    value holds a quote, so it counts only when a variable of that name is set."""
    if not _WINDOWS:
        return False
    names = {key.upper() for key in os.environ}
    for tok in words:
        text = str(tok)
        spaced = bool(re.search(r"\s", text))
        if '"' in text or (not spaced and re.search(r"[&|<>^]", text)):
            return True
        if any(not spaced or found.group(1).split(":", 1)[0].upper() in names for found in _CMD_NAME.finditer(text)):
            return True
    return False


def _with_run_time_words(words: list[str], route: Route, detail: str, launcher: bool = False) -> list[Route]:
    """An sf or Torque command whose words the shell can add to after the gate has
    read it (`sf data query -q ... -o dev $MORE`, where $MORE holds `-o prod`): the
    route the gate read still has to pass, and the consultant is asked as well, since
    the gate cannot check the words that are not there yet. A refused route stays
    refused, and Torque's own commands are refused outright (_built_at_run_time). A
    write stays a write alone: it needs an approval for this exact text, and a second
    route that only asks would let a yes at the prompt stand in for that approval."""
    if route.kind in ("admin", "org_write", "browser_write", *REFUSED_KINDS) \
            or not (_adds_words(words[1:]) or (launcher and _cmd_fills(words[1:]))):
        return [route]
    routes = [route, Route("unverifiable", route.org, detail + _ADDS_WORDS_NOTE, route.client)]
    names = _sf_org_flags(_topic(words[1:])) if launcher else TORQUE_ORG_FLAGS
    # An unquoted substitution can add org options to the outer command. Read the
    # literal options in its words as candidates, since its output can split again.
    for word in words[1:]:
        if not _EXPANSION_CHARS & set(_raw(word)):
            continue
        for nested in _substitutions(str(word)):
            tokens = _tokens(nested, _join=True)
            if tokens is not None:
                args = [part for token in tokens for made in _brace_expand(token.written)
                        for part in re.split(r"[ \t\n]+", str(_Word(made))) if part]
                routes += [Route("unverifiable", org, detail + _ADDS_WORDS_NOTE, route.client)
                           for org in org_values(args, names)]
    return routes


def _torque_routes(words: list[str], rest: list[str], detail: str) -> list[Route]:
    route = _torque(rest, detail, paren=bool(getattr(words[-1], "paren", False)))
    if rest and rest[0] in TORQUE_DELEGATED:
        return _with_run_time_words(words, route, detail)
    return [route]


def _segment_core(words: list[str], depth: int) -> list[Route]:
    first = words[0]
    head, rest = g._basename(first), words[1:]
    # The mark keeps a cut line from reading as if its last word were the problem, and a
    # cut never ends on an option, whose value would then read as "...".
    kept = words[:5]
    while len(words) > 5 and len(kept) > 1 and kept[-1].startswith("-"):
        kept = kept[:-1]
    detail = " ".join(kept) + (" ..." if len(words) > 5 else "")
    if "$" in first or "`" in first:
        return [Route("unverifiable", None, detail + " (command built at run time)")]
    if head == "env":
        i, unknown = 0, False
        while i < len(rest) and (rest[i].startswith("-") or _ASSIGN_RE.match(rest[i])):
            if rest[i] == "--":             # the end of env's options: assignments may follow, then the command
                i += 1
                while i < len(rest) and _ASSIGN_RE.match(rest[i]):
                    i += 1
                break
            if rest[i] in ("-S", "--split-string") or rest[i].startswith(("-S", "--split-string=")):
                return [Route("unverifiable", None, detail)]
            known = (_ASSIGN_RE.match(rest[i]) or rest[i] in _ENV_FLAGS + _ENV_VALUES
                     or rest[i].split("=", 1)[0] in _ENV_ATTACHED)
            took = 2 if rest[i] in _ENV_VALUES else 1
            word = str(rest[i])
            if word.startswith("-") and not word.startswith("--") and len(word) > 1:
                # Short options can share a word. A value option consumes its remaining
                # text, or the following word.
                known = True
                for at, letter in enumerate(word[1:]):
                    if letter == "S":
                        return [Route("unverifiable", None, detail)]
                    if "-" + letter in _ENV_VALUES:
                        took = 1 if at + 2 < len(word) else 2
                        break
                    if "-" + letter not in _ENV_FLAGS:
                        known = False
                        break
                known = known and not (_filled(rest[i]) or _LOOSE_CHARS & set(_raw(rest[i])))
            unknown = unknown or not known
            i += took
        inner = rest[i:]
        if not inner:
            return [Route("local", None, detail)]
        assigned = any(_ASSIGN_RE.match(w) for w in rest[:i])
        routes = _segment_core(inner, depth)
        if assigned:
            routes = _asked_as_well(routes, " (with a variable assignment)")
        if unknown:
            # an option this reading does not know could take the next word as its value
            routes = [Route("unverifiable", None, detail + " (an option of `env` the gate does not know)"), *routes]
        if any(w == "--chdir" or w.startswith("--chdir=") or re.match(r"-[i0v]*C", w)
               for w in rest[:i]):
            # The command runs in another folder than the one the gate reads paths from.
            routes = [Route("unverifiable", None, detail + " (runs in another folder)"), *routes]
        return routes
    if g._is_sf_token(first):
        route = _sf(rest, detail)
        found = _with_run_time_words(words, route, detail, launcher=True)
        if route.kind == "read" and route.data == "records" and _rest_reads_logs(_rest_path(rest)):
            found.append(replace(route, data="debug_logs"))
        return found
    if head in g.CONSOLE_SCRIPTS:
        if g.CONSOLE_SCRIPTS[head] == "torque":
            return _torque_routes(words, rest, detail)
        if rest[:1] in (["--help"], ["-h"], ["--version"]):
            return [Route("local", None, detail)]
        if head in TORQUE_CONSOLE_ROUTES:
            return _torque_routes(words, [TORQUE_CONSOLE_ROUTES[head], *rest], detail)
        return [Route("unverifiable", None, detail)]
    if head in ("powershell", "pwsh"):
        # Its command strings are read as a PowerShell tool call's are (the first text is
        # this line itself), so an owner command or a read inside them is seen.
        try:
            texts = g._powershell_commands(shlex.join(words))[1:]
        except ValueError as exc:
            return [Route("admin", None, f"{detail} (a PowerShell command the gate could not read: {exc})")]
        # The command it is given is PowerShell text: the words after -Command (or, with no
        # such option, its plain words), read with the same readings as a PowerShell call.
        given = next((i for i, word in enumerate(rest) if re.fullmatch(r"-c(o(m(m(a(nd?)?)?)?)?)?", word, re.I)), None)
        script = " ".join(rest[given + 1:] if given is not None else [w for w in rest if not w.startswith("-")])
        found = [r for text in texts for r in classify_bash(text, depth + 1) if r.kind != "local"]
        if script.strip():
            found += [r for r in _powershell_routes(script, depth + 1) if r.kind != "local"]
        return list(dict.fromkeys([Route("unverifiable", None, detail), *found]))
    if head in SHELLS:
        inner = g._shell_c_strings(words)
        i, given, no_run = 0, False, False
        while i < len(rest) and rest[i].startswith(("-", "+")):
            word = str(rest[i])
            if word in ("-", "--"):
                i += 1
                break
            if word in ("--help", "--version", "--dump-strings", "--dump-po-strings"):
                no_run = True
                break
            short = word[1:] if not word.startswith("--") else ""
            given = given or (word.startswith("-") and "c" in short)
            if "n" in short:
                no_run = word.startswith("-")
            no_run = no_run or (word.startswith("-") and "D" in short)
            # Other options can precede or follow -c. Its command is the first
            # remaining argument; -o and --rcfile consume their values.
            valued = word in ("--rcfile", "--init-file")
            optional = bool(set("oO") & set(short))
            i += 2 if i + 1 < len(rest) and (valued or (optional and
                not rest[i + 1].startswith(("-", "+")))) else 1
        if given and not no_run and i < len(rest) and rest[i] not in inner:
            inner.append(rest[i])
        if inner:
            # A shell running a string is still an interpreter the host must ask about;
            # the calls inside it are classified too.
            return [Route("unverifiable", None, detail), *[r for text in inner for r in classify_bash(text, depth + 1)]]
        if rest[:1] in (["--version"], ["--help"]):
            return [Route("local", None, detail)]
        return [Route("unverifiable", None, detail)]
    if g.PY_LAUNCHER_RE.match(head):
        module, after = g._python_module(rest)
        if module in g.TORQUE_MAIN_MODULES:
            return _torque_routes(words, rest[after:], detail)
        if module == "jsc_revert.cli":
            return _torque_routes(words, ["revert", *rest[after:]], detail)
        if rest[:1] in (["--version"], ["-V"]):
            return [Route("local", None, detail)]
        return [Route("unverifiable", None, detail)]
    if head == "eval":
        return [Route("unverifiable", None, detail), *classify_bash(" ".join(rest), depth + 1)]
    if head in BENIGN_WRAPPERS:
        options = []
        _, unknown = _wrapper_options(head, rest, options)
        if not unknown and {"--help", "--version"} & set(options):
            return [Route("local", None, detail)]
        inner = _strip_benign(head, words)
        if head == "command" and not _wrapper_options(head, rest)[1] and any(
                w.startswith("-") and not w.startswith("--") and set("vV") & set(w[1:])
                for w in rest[:_wrapper_options(head, rest)[0]]):
            return [Route("local", None, detail)]
        routes = _segment_core(inner, depth) if inner else [Route("local", None, detail)]
        if _wrapper_options(head, rest)[1]:
            routes = [Route("unverifiable", None, detail + f" (an option of `{head}` the gate does not know)"),
                      *routes]
        return routes
    if head in NETWORK or head in ("open", "xdg-open", "start"):
        if any(SF_HOSTS.search(w) for w in rest):
            return [Route("unverifiable", None, detail + " (reaches a Salesforce host)")]
        if head == "wget" and any(w.split("=", 1)[0] in ("--use-askpass", "-e", "--execute") or
                                  (w.startswith("-") and not w.startswith("--") and "e" in w[1:]) for w in rest):
            # wget starts the program `--use-askpass` names, and `-e` gives it a line of its
            # start-up file, which can name one.
            return [Route("unverifiable", None, detail + " (an option that names a program or a command to run)")]
        if head == "aria2c" and any(w.startswith("--on-") for w in rest):
            # aria2c's event hooks (`--on-download-complete=COMMAND`) run a command
            return [Route("unverifiable", None, detail + " (an option that names a program or a command to run)")]
        if head in NETWORK:
            return [Route("local", None, detail)]
    if head == "find" and any(w in ("-exec", "-execdir", "-ok", "-okdir") for w in rest):
        routes = [Route("unverifiable", None, detail)]
        for i, w in enumerate(rest):
            if w in ("-exec", "-execdir", "-ok", "-okdir") and i + 1 < len(rest):
                end = next((j for j in range(i + 1, len(rest)) if rest[j] in (";", "+", "\\;")), len(rest))
                routes += _segment(rest[i + 1:end], depth + 1)
        return routes
    if head in EXTENSIBLE_COMMANDS:
        return [Route("unverifiable", None, detail + " (can run hooks, plugins or configured commands)")]
    known_path = re.search(r"[/\\]", first) is None
    if head in ("alias", "unalias") and rest and (head == "unalias" or any("=" in word for word in rest)):
        return [Route("unverifiable", None, detail + " (changes what a command name runs)")]
    flags = rest[:rest.index("--")] if "--" in rest else rest
    if head == "unset" and any(w.startswith("-") and "f" in w[1:] for w in flags):
        return [Route("unverifiable", None, detail + " (removes a function that a command name runs)")]
    if head == "hash" and any(w.startswith("-") and set("pdr") & set(w[1:]) for w in flags):
        # Setting or forgetting a cached path changes the program a later call runs.
        return [Route("unverifiable", None, detail + " (changes the program a command name runs)")]
    if head in LOCAL_COMMANDS and head != "[[" and known_path:
        return [Route("local", None, detail)]
    routes = [Route("unverifiable", None, detail)]
    start = _inner_start(words)
    if start is not None:
        routes += _segment(words[start:], depth + 1)
    return routes


# Variables Bash itself runs commands from: PS4 before each traced command (`set -x`), the
# prompts and PROMPT_COMMAND in an interactive shell, BASH_ENV and ENV in a shell it starts.
_BASH_RUNS = frozenset({"PS0", "PS1", "PS2", "PS4", "PROMPT_COMMAND", "BASH_ENV", "ENV"})
# Names programs and the shell read whether or not this session's environment has them
# yet: where commands and the home folder are found, how words split, what sf, Node and
# Torque are configured by, proxies and certificates. A shell tells `path` from `PATH`
# (checked in Git Bash, on Windows too), so the names are matched as written: the
# upper-case ones, the lower-case spellings programs read as well (`http_proxy`,
# `npm_config_...`), and `path` and `cdpath`, which zsh ties to PATH and CDPATH.
_READ_NAMES = re.compile(r"(?:PATH|HOME|IFS|CDPATH|EXECIGNORE|GLOBIGNORE|POSIXLY_CORRECT|SHELLOPTS|BASHOPTS|"
                         r"BASH_XTRACEFD|TMPDIR|TEMP|TMP|DEBUG|PWDEBUG|USERPROFILE|HOMEDRIVE|HOMEPATH|APPDATA|"
                         r"LOCALAPPDATA|COMSPEC|PATHEXT|(?:SF|SFDX|TORQUE|JSC|NODE|NPM|npm|LD|DYLD|XDG|GIT|SSL|CURL|"
                         r"OPENSSL)_\w*|PYTHON\w*|\w*_PROXY|\w*_proxy|path|cdpath)\Z")
_ASSIGNING_BUILTINS = frozenset({"read", "printf", "declare", "typeset", "local", "readonly", "export", "unset",
                                 "let", "getopts", "mapfile", "readarray", "wait"})
# Places where Bash evaluates arithmetic, and arithmetic can assign: `$[ NAME = 1 ]` (the old
# form) and an array index, wherever a name with one is taken (`${a[NAME=1]}`, `printf -v
# 'a[NAME=1]'`, `read 'a[NAME=1]'`): a bracket directly after a name or a `$`. The brackets
# of `[ ... ]` and `[[ ... ]]` are not these. `$(( ))` and `(( ))` are read as commands
# the gate does not know, and are asked about for that.
_ARITHMETIC = re.compile(r"(?<=[A-Za-z0-9_$])\[([^\[\]\n]*)\]")
_ARITHMETIC_SETS = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:\+\+|--|(?:[-+*/%&|^]|<<|>>)?=(?!=))"
                              r"|(?:\+\+|--)\s*([A-Za-z_][A-Za-z0-9_]*)")
# `{NAME}>file`: Bash opens the file on a descriptor of its choosing and stores the number in NAME.
_DESCRIPTOR_NAME = re.compile(r"(?:^|[\s;|&(])\{([A-Za-z_][A-Za-z0-9_]*)(?:\[[^\n]*\])?\}(?=[<>])")
# `${NAME:=value}` and `${NAME=value}`: an expansion that assigns when the variable is unset
# (or, with the colon, empty), also to one element of an array.
_PARAMETER_SETS = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:\[[^\]\n]*\])?:?=")
_NAME_AT_START = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(?:\+?=|\[|\Z)")


def _set_in_passing(command: str) -> list[str]:
    """The names a line sets without an assignment statement: by an expansion that assigns
    (`echo ${HOME:=/x}`, when the variable is unset or empty), in arithmetic (`$[ HOME = 9 ]`,
    `${a[HOME=1]}`; the value is a number, and `PATH=3` makes a folder named 3 the place
    commands are found) and by a redirection that stores its descriptor (`{HOME}>file`).
    Checked in real Bash. Read from the line as written: inside double quotes they are
    evaluated all the same."""
    command = command.replace("\\\n", "")
    names = [first or second for inside in _ARITHMETIC.findall(command)
             for first, second in _ARITHMETIC_SETS.findall(inside)]
    return names + _DESCRIPTOR_NAME.findall(command) + _PARAMETER_SETS.findall(command)


def _assigned_names(words: list[str]) -> list[str]:
    """The names a statement sets, or unsets, in the shell itself, so that the value is
    there for the commands after it: `NAME=value` with no command after it, the variable
    of a `for` or `select`, and the names given to read, printf -v, declare, typeset,
    local, readonly, export, unset, let, getopts, mapfile and readarray (after wrapper
    words such as command or builtin). A name the shell builds (`printf -v "$n"`) is given
    as `$`: it can be any name. A `NAME=value` before a command is set for that command
    alone, and is not one of these."""
    if words and _is_keyword(words[0]) and words[0] in ("for", "select"):
        return [str(words[1])] if len(words) > 1 else []
    names, literal = [], False
    while words:
        if g._basename(words[0]) in BENIGN_WRAPPERS:
            options = []
            _, unknown = _wrapper_options(g._basename(words[0]), words[1:], options)
            if not unknown and ({"--help", "--version"} & set(options) or
                                g._basename(words[0]) == "command" and {"-v", "-V"} & set(options)):
                return []               # Query and help options do not run the assigning builtin named after them.
            words = _strip_benign(g._basename(words[0]), words)
            literal = True             # a wrapper's target is a command word, not shell syntax
        elif not literal and _is_keyword(words[0]):
            words = words[1:]
        elif not literal and _is_assignment(words[0]):
            names.append(words[0].split("=", 1)[0])
            words = words[1:]
        else:
            break
    if not words:
        return names
    head, rest = words[0], words[1:]
    if head == "[[":
        # Both operands of a numeric comparison are arithmetic expressions, where
        # an assignment or increment changes the shell's variable for later calls.
        compare = {"-eq", "-ne", "-lt", "-le", "-gt", "-ge"}
        return [first or second for i, word in enumerate(rest)
                if (i and rest[i - 1] in compare) or (i + 1 < len(rest) and rest[i + 1] in compare)
                for first, second in _ARITHMETIC_SETS.findall(str(word))]
    if head not in _ASSIGNING_BUILTINS:
        return []
    options_end = next((i for i, word in enumerate(rest) if word == "--" or
                        not word.startswith(("-", "+"))), len(rest))
    if head in ("declare", "typeset", "local", "readonly", "export", "unset") and "--help" in rest[:options_end]:
        return []                       # Builtin help exits before changing a named variable.
    if head in ("declare", "typeset", "local") and any(
            word.startswith(("-", "+")) and not word.startswith("--") and "p" in word[1:]
            and not (_filled(word) or _LOOSE_CHARS & set(_raw(word)))
            for word in rest[:options_end]):
        return []                       # Printing declarations does not change their values or attributes.
    if head in ("printf", "read", "wait"):
        flags, values, name_option = {"printf": ("", "v", "v"),
                                     "read": ("ersE", "adinNptu", "a"),
                                     "wait": ("fn", "p", "p")}[head]
        given, i = [], 0
        while i < len(rest) and rest[i].startswith("-") and len(rest[i]) > 1:
            word = rest[i]
            if word == "--":
                i += 1
                break
            if word == "--help":
                return []
            if _filled(word) or _LOOSE_CHARS & set(_raw(word)):
                return ["$"]            # A computed option can choose any variable name.
            took = 1
            for at, letter in enumerate(word[1:]):
                if letter in values:
                    attached = at + 2 < len(word)
                    value = _Word(_raw(word)[at + 2:]) if attached else rest[i + 1:i + 2]
                    if letter == name_option:
                        given += [value] if attached else value
                    took = 1 if attached else 2
                    break
                if letter not in flags:
                    return ["$"]        # Unknown options retain the conservative setter reading.
            i += took
        if head == "read":
            given += rest[i:]
        rest = []                       # Option values and printf's format and data are not names.
    else:
        given = [word for word in rest if not word.startswith(("-", "+"))]
    names = []
    for word in rest:
        # An option written together with its value (`printf -vHOME x`, `read -aHOME`, `wait
        # -pHOME`): every ending of an option word that is a name could be the name it sets.
        if word.startswith("-") and not (_filled(word) or _LOOSE_CHARS & set(_raw(word))):
            names += [str(word)[at:] for at in range(2, len(word)) if _NAME_AT_START.fullmatch(str(word)[at:])]
        elif word.startswith("-"):
            names.append("$")
    for word in given:
        if _filled(word) or _LOOSE_CHARS & set(_raw(word)):
            names.append("$")           # the shell builds the name: it can be any name
            continue
        name = _NAME_AT_START.match(str(word))
        if name:
            names.append(name.group(1))
            if head in ("declare", "typeset", "local") and _NAME_AT_START.fullmatch(str(word).split("=", 1)[-1]):
                names.append(str(word).split("=", 1)[-1])       # `declare -n ref=PATH`: ref is PATH from here on
    return names


def _read_by_later_commands(name: str) -> bool:
    """A variable whose value reaches the commands after it once the shell holds it: one
    that is in the environment already (an assignment to an exported variable changes
    the environment), or one of the names programs and the shell read (_READ_NAMES).
    The name is compared as written: `tmp=x` does not touch TMP. On Windows the gate
    sees its own environment's names in upper case only, so there a name with a capital
    in it is compared without case (`Path`, `ProgramData`), and an all-lower-case one,
    the usual spelling of a variable of the line's own, is not."""
    if name == "$" or _READ_NAMES.match(name):
        return True
    names = set(os.environ)
    return name in names or (_WINDOWS and not name.islower() and name.upper() in names)


def _exports(words: list[str]) -> bool:
    """The segment puts variables into the environment of later commands (after any
    keywords, assignments and wrapper words such as command, builtin or time): `export`,
    `declare -x`/`typeset -x`/`local -x`/`readonly -x` (flags combined or not),
    `set -a`/`set -o allexport` (every later assignment is exported), or `set -k`/`set
    -o keyword` (a `NAME=value` word anywhere in a later command is set for it)."""
    literal = False
    while words:
        if g._basename(words[0]) in BENIGN_WRAPPERS:
            # `command export ...`, `builtin declare -x ...`, `time -p export ...`
            options = []
            _, unknown = _wrapper_options(g._basename(words[0]), words[1:], options)
            if not unknown and ({"--help", "--version"} & set(options) or
                                g._basename(words[0]) == "command" and {"-v", "-V"} & set(options)):
                return False            # Query and help options do not export anything.
            words = _strip_benign(g._basename(words[0]), words)
            literal = True
        elif not literal and (_is_keyword(words[0]) or _is_assignment(words[0])):
            words = words[1:]
        else:
            break
    if not words:
        return False
    head, rest = words[0], words[1:]
    options_end = next((i for i, word in enumerate(rest) if word == "--" or
                        not word.startswith(("-", "+"))), len(rest))
    flags = rest[:options_end]
    if head in ("export", "declare", "typeset", "local", "readonly") and "--help" in flags:
        return False
    if head == "set":
        flags = [w for w in rest if w.startswith(("-", "+")) and w not in ("-", "--")]
    operands = rest[options_end + (options_end < len(rest) and rest[options_end] == "--"):]
    if head == "export":
        return bool(operands)            # With no names export only prints the current declarations.
    if head in ("declare", "typeset", "local", "readonly"):
        if head != "readonly" and any(not f.startswith("--") and "p" in f[1:] and
                                     not (_filled(f) or _LOOSE_CHARS & set(_raw(f))) for f in flags):
            return False
        return bool(operands) and any(f.startswith("-") and "x" in f[1:] for f in flags)
    if head == "set":
        rest = words[1:]
        return any(f.startswith("-") and not f.startswith("--") and set("ak") & set(f[1:]) for f in flags) or any(
            rest[i] == "-o" and i + 1 < len(rest) and rest[i + 1] in ("allexport", "keyword") for i in range(len(rest)))
    return False


# What the net under the substitution reader keeps: the routes of commands the gate
# knows. A stray word of the tail that looks like no command is not asked about.
_NET_KINDS = (*REFUSED_KINDS, "admin", "read", "check_only", "org_write", "browser_write", "no_org")


_BASH_READINGS: ContextVar[dict[tuple[str, int, bool], list[Route]] | None] = ContextVar(
    "torque_bash_readings", default=None)


def _bounded_bash(reader):
    """Reuse readings within one call, refusing text that exceeds the reader's budget."""
    @wraps(reader)
    def bounded(command: str, _depth: int = 0, _net: bool = True) -> list[Route]:
        readings = _BASH_READINGS.get()
        token = None
        if readings is None:
            readings = {}
            token = _BASH_READINGS.set(readings)
        try:
            key = (command, _depth, _net)
            if key not in readings:
                refused = [Route("admin", None, "Bash command exceeds the reader's complexity limit")]
                if len(readings) >= 128:
                    return refused
                readings[key] = refused
                try:
                    readings[key] = reader(command, _depth, _net)
                except RecursionError:
                    pass
            return list(readings[key])
        finally:
            if token is not None:
                _BASH_READINGS.reset(token)
    return bounded


@_bounded_bash
def classify_bash(command: str, _depth: int = 0, _net: bool = True) -> list[Route]:
    """Routes for one shell command string, in order, without duplicates."""
    if _depth > 8:
        return [Route("unverifiable", None, "nesting too deep")]
    # The words are split from the line as written: _mask decodes $'...' where the shell
    # does. The decoded text is kept for the looser scans below, which read more.
    text = g._decode_ansi_c(command or "")
    parsed = _split(command or "")
    routes: list[Route] = []
    if parsed is None:
        # Unbalanced quoting (a heredoc body, for example): read it the naive way,
        # which splits more, never less. Whether a backslash at a line end continues the
        # line cannot be told without the quoting, so the text is read both ways: as it
        # stands, and with the continuations taken out and a file descriptor's digits
        # with them (`-o 1\<newline>>&2 org` names org).
        segments = [g._without_redirections(toks) for toks, _ in g._segments_with_separators(text) if toks]
        joined = _NAIVE_DESCRIPTOR.sub("", text.replace("\\\n", ""))
        if joined != text:
            segments += [g._without_redirections(toks) for toks, _ in g._segments_with_separators(joined) if toks]
        # Bash runs the commands of a line before it reads the next one, so the lines before
        # the one whose quotes do not pair still run (checked in real Bash). The longest run
        # of whole lines from the start that does read is read as written as well: the naive
        # reading leaves a quote or a backslash inside a word in it (`-o o"r"g` names org).
        scanned = _scan(command or "")
        before = (command or "").rfind("\n", 0, len(command or "") if scanned[2] is None else scanned[2])
        heads = dict.fromkeys(((command or "")[:max(0, before)], (command or "")[:scanned[3]]))
        read = False
        for head in heads:
            if head.strip() and _split(head) is not None:
                routes += classify_bash(head, _depth + 1, _net)
                read = True
        if not read:
            # If another quoting form prevents those readings, each whole line is read
            # on its own. Their total length is bounded by the text, unlike prefixes.
            for line in dict.fromkeys((command or "")[:max(0, before)].split("\n")):
                if _split(line) is not None:
                    routes += classify_bash(line, _depth + 1, _net=False)
    else:
        segments = parsed[0]
    exported = ""
    passing = _set_in_passing(command or "")
    if any(_read_by_later_commands(name) for name in passing):
        exported = " (after a variable that the commands after it read was set)"
    if _BASH_RUNS & set(passing):
        routes.append(Route("unverifiable", None, "${" + sorted(_BASH_RUNS & set(passing))[0]
                            + "...} (Bash runs commands from this variable)"))
    joined = _split(command or "", _join=True)
    for words in segments:
        found = _segment(words, _depth)
        if parsed is not None and joined is not None and joined[0] != parsed[0]:
            # The whole outer command is read below. A substitution can truncate this
            # reading before its org option, so it cannot establish a missing org.
            found = [route for route in found if route.kind != "no_org"]
        if exported:
            # An exported variable (DEBUG=pw:api prints the session URL, PWDEBUG forces a
            # visible browser, SF_* can point an alias at another org) reaches every later
            # program of the shell, so the gate cannot check what they do. So does a plain
            # assignment to a variable that is exported already (`PATH=/x:$PATH; sf ...`
            # runs another sf, `HOME=/x; sf ...` reads other aliases; checked in real Bash).
            found = _asked_as_well(found, exported)
        routes.extend(found)
        pieces = [_brace_expand(getattr(word, "written", _raw(word))) for word in words]
        if any(made != [getattr(word, "written", _raw(word))] for made, word in zip(pieces, words)):
            # Brace expansion is worked out from the text of the line alone, so the words it
            # makes are read as well: `{sf,data,query,-o,B}` is the command `sf data query -o
            # B`, with its org and its data class. (The reading above still asks about the
            # braces.) A write both readings find is one write, as for PowerShell's readings,
            # and a command both refuse is refused once.
            made = _segment([_Word(piece) for made in pieces for piece in made], _depth)
            made = _asked_as_well(made, exported) if exported else made
            counted = {(route.kind, route.org) for route in routes if route.kind in ("org_write", "browser_write", "admin")}
            routes.extend(route for route in made if route.kind != "local" and (route.kind, route.org) not in counted)
        names = _assigned_names(words)
        if _BASH_RUNS & set(names):
            routes.append(Route("unverifiable", None, " ".join(words[:5]) + " (Bash runs commands from this variable)"))
        if not exported and _exports(words):
            exported = " (after an exported variable)"
        elif not exported and any(_read_by_later_commands(name) for name in names):
            exported = " (after a variable that the commands after it read was set)"
    # An unquoted substitution does not end the outer command. Read its whole words
    # as well, retaining the original reading and adding only org-capable routes.
    if joined is not None and (parsed is None or joined[0] != parsed[0]):
        joined_exported = " (after a variable that the commands after it read was set)" if any(
            _read_by_later_commands(name) for name in passing) else ""
        counted = {(route.kind, route.org, route.data) for route in routes}
        for words in joined[0]:
            found = _segment(words, _depth)
            pieces = [_brace_expand(getattr(word, "written", _raw(word))) for word in words]
            if any(made != [getattr(word, "written", _raw(word))] for made, word in zip(pieces, words)):
                found += _segment([_Word(piece) for made in pieces for piece in made], _depth)
            found = _asked_as_well(found, joined_exported) if joined_exported else found
            for route in found:
                if (route.kind in _NET_KINDS or route.kind == "unverifiable" and (
                        route.org is not None or words and "\r" in words[0])) \
                        and (route.kind, route.org, route.data) not in counted:
                    routes.append(route)
                    counted.add((route.kind, route.org, route.data))
            if not joined_exported and _exports(words):
                joined_exported = " (after an exported variable)"
            elif not joined_exported and any(_read_by_later_commands(name) for name in _assigned_names(words)):
                joined_exported = " (after a variable that the commands after it read was set)"
    setting = _CONTAINER_SET_RE.search(text)
    if setting:
        # NAME=true sf org open, export NAME=..., $env:NAME = ...: with it set, an
        # approved `sf org open` prints its login URL instead of opening a browser.
        routes.append(Route("admin", None, f"setting {setting.group(1).upper()} makes `sf org open` print its "
                                           "login URL"))
    # What a substitution runs is a command too. The looser scan pairs parentheses
    # without reading quotes, so `"$(echo ")" ; sf data query ...)"` would end at the
    # quoted one: the substitutions are also found with their own quotes.
    nested_texts = list(dict.fromkeys([*g._direct_substitutions(text), *_substitutions(command or "")]))
    for nested in sorted(nested_texts, key=len) if len(nested_texts) > 8 else nested_texts:
        routes.extend(r for r in classify_bash(nested, _depth + 1) if r.kind != "local")
    # A word is read once more as it stands with its quotes removed: `'a[$'"(sf ...)]"` is
    # the text `a[$(sf ...)]`. Where Bash evaluates a word's text a second time, the
    # substitution in it runs: an array subscript given to read, printf -v, unset, declare,
    # `test -v` or `[[ ]]`, a value put into PS4 or expanded with `${y@P}` (checked in real
    # Bash). The gate does not follow where a value goes; it reads every word this way.
    for word in dict.fromkeys(str(word) for words in segments for word in words if "$(" in word or "`" in word):
        for nested in dict.fromkeys([*g._direct_substitutions(word), *_substitutions(word)]):
            routes.extend(r for r in classify_bash(nested, _depth + 1) if r.kind != "local")
    if "\r" in (command or ""):
        # Bash on Unix keeps literal CR characters. Git Bash on Windows drops them
        # before reading the script (checked there), so read the command without them
        # as well. This also joins a backslash before CR LF.
        routes.extend(r for r in classify_bash(command.replace("\r", ""), _depth + 1) if r.kind != "local")
    if _net:
        for tail in _substitution_tails(command or ""):
            routes.extend(r for r in classify_bash(tail, _depth + 1, _net=False) if r.kind in _NET_KINDS)
        if ("<<" in (command or "") or _HEREDOC_OPERATOR.search(command or "")) and "\n" in command:
            # The net under the here-document reader: each line on its own, in a fresh
            # quoting context. Wherever a body really ends, the command after it begins
            # a line. (A body's own lines are read as commands in any case.)
            for tail in dict.fromkeys(_continued_heredoc_tails(command)):
                routes.extend(r for r in classify_bash(tail, _depth + 1, _net=False) if r.kind in _NET_KINDS)
            for line in dict.fromkeys(command.splitlines()):
                routes.extend(r for r in classify_bash(line, _depth + 1, _net=False) if r.kind in _NET_KINDS)
    return list(dict.fromkeys(routes)) or [Route("local", None, "")]


def mcp_org(tool_input: dict) -> str | None:
    values = [tool_input[k] for k in MCP_ORG_KEYS if isinstance(tool_input.get(k), str) and tool_input[k]]
    values = list(dict.fromkeys(values))
    return values[0] if len(values) == 1 else None


def _words(name: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return [w for w in re.split(r"[^A-Za-z0-9]+", spaced.casefold()) if w]


def classify_mcp(tool_name: str, tool_input: dict) -> Route:
    tool = tool_name.split("__")[-1]
    words = set(_words(tool))
    # The call's own name and, for a resource read, the server its arguments name.
    names = g._mcp_names(tool_name, tool_input)
    surfaces = {g._mcp_surface(name) for name in names}
    if "desktop" in surfaces:
        action = str(tool_input.get("action") or "")
        if words <= BROWSER_READ_WORDS | {"cursor", "position", "zoom", "screenshot"} or \
                (tool == "computer" and action in COMPUTER_READ_ACTIONS):
            return Route("browser_read", None, tool_name, data="records")
        return Route("admin", None, tool_name + " (desktop control can reach a terminal)")
    if "browser" in surfaces:
        if tool == "computer":
            action = str(tool_input.get("action") or "")
            return Route("browser_read" if action in COMPUTER_READ_ACTIONS else "browser_write", None,
                         tool_name, data="records")
        return Route("browser_read" if words and words <= BROWSER_READ_WORDS else "browser_write", None,
                     tool_name, data="records")
    if any(g._mcp_reaches_salesforce(name) for name in names):
        if words & MCP_CREDENTIAL_WORDS or ("display" in words and words & {"org", "user"}):
            return Route("credential", None, tool_name)
        if "orgs" in words and words & {"list", "all"}:
            return Route("all_orgs", None, tool_name)
        org = mcp_org(tool_input)
        # Resuming scratch org creation also authenticates and deploys its settings.
        scratch = tool == "resume_tool_operation" and str(tool_input.get("jobId") or "").startswith("2SR")
        if words & MCP_WRITE_WORDS or not words & MCP_READ_WORDS or scratch:
            return Route("org_write" if org else "no_org", org, tool_name)
        data = ("debug_logs" if words & {"log", "logs", "debug"} else
                "records" if words & MCP_RECORD_WORDS else None)
        # A SOQL tool can read logs without naming them in the tool's name.
        query = tool_input.get("query")
        if data == "records" and words & {"query", "soql"} and isinstance(query, str):
            import urllib.parse
            if rest_data_class("/query?q=" + urllib.parse.quote(query, safe="")) == "debug_logs":
                data = "debug_logs"
        return Route("read" if org else "no_org", org, tool_name, data=data)
    host = g._salesforce_host(tool_input)
    if host:
        # A server under a neutral name: only its arguments show that it reaches an org.
        return Route("unverifiable", None, f"{tool_name} (names the Salesforce host {host})")
    return Route("local", None, tool_name)


# Routes a PowerShell reading adds to the plain one: the refusals, and the reads, so
# that a read inside `powershell -Command '...'` still meets the session's client
# and the consent's data classes. Writes are left to the plain reading: the same
# write read twice would no longer be the one approved command.
_POWERSHELL_KINDS = (*REFUSED_KINDS, "admin", "read", "check_only", "no_org")


_PS_HERE = re.compile(r"@([\"'])[ \t]*(?:\r\n?|\n)")
# Characters PowerShell's tokenizer reads as a quote, a dash or a space. The list is the
# tokenizer's own: tests/test_gate_against_powershell.py asks it about every character of
# the Basic Multilingual Plane. The line and paragraph separators (U+2028, U+2029) separate
# words for it; they do not end a statement.
_PS_CHARACTERS = str.maketrans({
    **dict.fromkeys("\u2018\u2019\u201a\u201b", "'"), **dict.fromkeys("\u201c\u201d\u201e", '"'),
    **dict.fromkeys("\u2013\u2014\u2015", "-"),
    **dict.fromkeys("\u00a0\u0085\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
                    "\u2028\u2029\u202f\u205f\u3000\f\v", " ")})
_PS_TOKEN_START = " \t\r\n;|&(){}"
# After a comma the array continues through blank lines, comments and line continuations.
_PS_AFTER_COMMA = re.compile(r"(?:[ \t\r\n]|`(?:\r\n?|\n)|#[^\r\n]*(?:\r\n?|\n|\Z)|<#[\s\S]*?#>)*")
# These backtick escapes hold control characters, also outside double quotes.
_PS_ESCAPE_VALUES = {"0": "\0", "a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}
# Put after a word that Windows PowerShell may not hand to a native program as the one
# word it is in PowerShell: an unquoted expansion, so the word counts as one the shell
# can turn into several (_adds_words, _built_at_run_time).
_PS_NATIVE = "${native}"
_PS_CMD_CHARS = frozenset("&|<>^")
# PowerShell's redirections: `>`, `>>`, `2>`, `2>>`, `2>&1`, `*>`, `<`. A stream's number
# counts only at the start of a token, and only 1 to 6 (`7>x` is a word).
_PS_REDIRECTION = re.compile(r"[*1-6]?>>?(?:&[12])?|<")
# How many places of a line the gate reads each way (_powershell_ways).
_PS_EACH_WAY = 3


def _ps_inside(text: str, start: int) -> tuple[str, int]:
    """(what stands between a parenthesis open before `start` and the `)` that closes it,
    counting parentheses and nothing else; the index after that `)`). To the end of the
    text when nothing closes it."""
    depth = 1
    for at in range(start, len(text)):
        depth += (text[at] == "(") - (text[at] == ")")
        if not depth:
            return text[start:at], at + 1
    return text[start:], len(text)


def _ps_here_end(command: str, opener: re.Match) -> re.Match | None:
    """The closing line of a here-string, including its whole line ending."""
    ending = re.compile(r"(?:\r\n?|\n)" + re.escape(opener.group(1)) + "@")
    found = ending.search(command, opener.end() - 1)
    while (found and opener.group(1) == '"' and found.group().startswith("\n")
           and (found.start() - len(command[:found.start()].rstrip("`"))) % 2):
        # A backtick before LF escapes the closing line; before CR LF it takes only the CR.
        found = ending.search(command, found.end())
    return found


def _ps_target(command: str, start: int, choices: tuple, places: list) -> int | None:
    """The end of a structured redirection target, or None for a plain word. A group,
    member or index belongs to the filename; the word after that expression does not.
    Generic filenames can hold a subexpression but not a bare group."""
    def variable(at: int) -> int:
        if command[at + 1:at + 2] == "{":
            at += 2
            while at < len(command) and command[at] != "}":
                at += 2 if command[at] == "`" else 1
            return min(at + 1, len(command))
        if command[at + 1:at + 2] in ("$", "?", "^"):
            return at + 2
        at += 1
        while at < len(command) and (command[at].isalnum() or command[at] in "_:") \
                and not command.startswith("::", at):
            at += 1
        return at

    def quoted(at: int) -> int:
        here = _PS_HERE.match(command, at)
        if here:
            stop = _ps_here_end(command, here)
            return len(command) if stop is None else stop.end()
        mark, at = command[at], at + 1
        while at < len(command):
            if command[at] == "`" and mark == '"':
                at += 2
            elif command.startswith("${", at) and mark == '"':
                at = variable(at)
            elif command.startswith("$(", at) and mark == '"':
                at = _ps_inside(command, at + 2)[1]
            elif command[at] == mark:
                if command[at + 1:at + 2] != mark:
                    return at + 1
                at += 2
            else:
                at += 1
        return len(command)

    def group(at: int) -> int:
        pairs = {"(": ")", "[": "]", "{": "}"}
        stack = [(pairs[command[at]], command[at - 1:at] == "$" or command[at] == "[")]
        at, fresh, settled = at + 1, True, False
        while at < len(command):
            c = command[at]
            if c == "`":
                at += 2
                fresh = False
                continue
            if c in "'\"" or _PS_HERE.match(command, at):
                at = quoted(at)
                continue
            if command.startswith("${", at):
                at, fresh = variable(at), False
                continue
            if c == "#" or command.startswith("<#", at):
                own = fresh
                if not fresh and not settled:
                    places.append(at)
                    own = len(places) <= len(choices) and choices[len(places) - 1]
                    settled = not own
                if own:
                    stop = (command.find("#>", at + 2) if c == "<"
                            else min((command.find(mark, at) for mark in "\r\n"
                                      if command.find(mark, at) >= 0), default=-1))
                    at = len(command) if stop < 0 else stop + (2 if c == "<" else 1)
                    fresh, settled = True, False
                    continue
                if c == "<":
                    at += 2
                    continue
            if c in pairs:
                stack.append((pairs[c], command[at - 1:at] == "$" or c == "["))
                fresh, settled = True, False
            elif c == stack[-1][0]:
                fresh = not stack.pop()[1]
                if not stack:
                    return at + 1
                settled = False
            elif c in " \t\r\n;|&":
                fresh, settled = True, False
            else:
                fresh = False
            at += 1
        return len(command)

    at = start
    while at < len(command):
        if command[at] in " \t\r\n":
            at += 1
        elif command[at:at + 2] == "`\n" or command[at:at + 3] == "`\r\n":
            at += 2 if command[at + 1] == "\n" else 3
        elif command.startswith("<#", at):
            stop = command.find("#>", at + 2)
            at = len(command) if stop < 0 else stop + 2
        elif command[at] == "#":
            stop = min((command.find(mark, at) for mark in "\r\n"
                        if command.find(mark, at) >= 0), default=-1)
            at = len(command) if stop < 0 else stop + 1
        else:
            break
    if at == len(command):
        return None
    first, computed, member = at, False, False
    if command[at] in "({" or command[at:at + 2] in ("$(", "@(", "@{"):
        at = group(at + (command[at] in "$@"))
        computed, member = True, True
    elif command[at] in "'\"" or _PS_HERE.match(command, at):
        computed = bool(_PS_HERE.match(command, at))
        at, member = quoted(at), True
    elif command[at] == "$" and variable(at) > at + 1:
        at = variable(at)
        member = True
    if member:
        while at < len(command):
            if command[at] == "[":
                at, computed = group(at), True
            elif command[at] == "." or command.startswith("::", at):
                width = 2 if command.startswith("::", at) else 1
                if command[at + width:at + width + 1] in ("", " ", "\t", "\r", "\n"):
                    break
                at += width
                if command[at] in "'\"":
                    at = quoted(at)
                elif command[at] == "(":
                    at = group(at)
                elif command[at] == "$":
                    at = group(at + 1) if command[at + 1:at + 2] == "(" else variable(at)
                else:
                    while at < len(command) and (command[at].isalnum() or command[at] == "_"):
                        at += 1
                computed = True
                if command[at:at + 1] in ("(", "{"):
                    at = group(at)
            else:
                break
        if computed:
            return at
        if command[first] in "'\"(":
            return None
    while at < len(command) and command[at] not in " \t\r\n;|&(){}<>,":
        if command[at] == "`":
            at += 2
        elif command[at] in "'\"":
            at = quoted(at)
        elif command.startswith("$(", at):
            at, computed = group(at + 1), True
        elif command.startswith("${", at):
            at = variable(at)
        else:
            at += 1
    return at if computed else None


def _powershell_escapes(command: str, braces: bool = False, commas: bool = False,
                        assigned: list | None = None, choices: tuple = (), places: list | None = None) -> str:
    """A PowerShell line rewritten in Bash's quoting, so that its words split as
    PowerShell passes them: a backtick quotes the next character or gives a letter
    escape its control value, a doubled quote inside quotes is one quote, a backtick at
    a token's start continues a line while one inside a word keeps the line-end
    character, a backslash is ordinary, a block comment
    (`<# ... #>`) is nothing, a here-string (`@"` or `@'` at a line end, to a line
    that begins with `"@` or `'@`) is one quoted string whatever quotes it holds,
    `${any name}` is one variable whatever its name holds, a `$` before a quote is a
    plain `$` (Bash would start another kind of string there), what follows the
    stop-parsing token `--%`, to a pipe outside double quotes or the line's end, is passed on as it stands,
    a comma with a space beside it separates words, inside `"... $( ... ) ..."` the
    text is code again, with quotes of its own, and a word that begins with a quoted
    string ends at the closing quote (`"x"y` is two words, `x"y"z` is one).

    With `braces`, a brace ends a statement: what a script block or a hash literal
    holds is read as statements (`&{sf ...}`, `echo @{rows = sf ...}`), and so does the
    first `=` of a hash literal's entry, whatever its key is (`@{1=sf ...}`); a key that is
    a bare name is written in quotes, since it is text and never a command (`@{name = ...}`).
    With `commas`, every comma outside quotes separates words: one comma with a space
    beside it makes the whole list an array (`'a',--target-org, B`). `assigned` gets an
    item for each assignment met (`$x = ...`, `$env:NAME = ...`, `[int]$n = ...`).

    Windows PowerShell builds one command line for a native program and does not
    escape what an argument holds, so the program can receive other words than
    PowerShell had: a double quote inside an argument ends it there (`'x" --target-org
    B'` arrives as `x` and `--target-org B`), so can the value of a variable inside a
    double-quoted string, a backslash before the closing quote swallows the words after
    it, and a `.cmd` launcher reads `&`, `|`, `<`, `>` and `^` in an argument without
    spaces. A string with any of these gets the mark _PS_NATIVE after it.

    A `#`, a `<#`, a `<` or a `>` at the start of a token is PowerShell's own: a comment, a
    redirection. In the middle of a word PowerShell decides by what it is reading. Among
    the arguments of a command the character is part of the word (`echo a#b`, `echo $x>b`
    and `sf ... -o a<#b#>` each pass one word); in an expression it is the comment or the
    redirection (`$n = 1#b`, `$x>b`). The gate does not follow which of the two PowerShell
    is reading, so the caller reads the line each way (_powershell_ways): `choices` says
    which way at each such place in turn (true: PowerShell's own; none left: part of the
    word), and `places` gets an item for each place met. A subexpression in the middle of
    a word is such a place too, when what it holds would end elsewhere as a token of its
    own (`a$("x)")` is one word; after a parameter's name the same text is a parenthesis)."""
    out, quote, i = [], "", 0
    places = [] if places is None else places
    risky = spaced = odd = False        # of the string being read
    last = ""                           # its last character
    opened = 0                          # where it began in `out`
    fresh = False                       # it began a word
    nested: list = []                   # the open braces: "block", or where a hash literal's entry is ("key", "value")
    later: list = []                    # text to read once more as code, after a `--%`
    targets: list = []                  # a computed filename still evaluates what it holds
    head = ""                           # the first character of the statement being read
    entry = 0                           # where the key of a hash literal's entry began in `out`
    joined = -1                         # how long `out` was right after a piece of a word that goes on
    apart = -1                          # how long `out` was right after a token that ends where it is written
    settled = False                     # the word being read holds a character read as part of it

    def subexpression(text: str, start: int, strings: bool) -> tuple[str, int]:
        """(the `$(...)` whose inside begins at `start` in `text`, rewritten; the index after
        its `)`). Inside a string, a here-string or a word, PowerShell's tokenizer finds the
        end of a subexpression by counting parentheses and nothing else: one inside quotes
        or in a comment counts too. Outside a here-string (`strings`) it then takes a
        doubled double quote, or one after a backtick, for one quote, and reads what it has
        as code. (Checked against the tokenizer.) After a comment the closing parenthesis is
        written on a line of its own, so the comment does not take it."""
        inner, stop = _ps_inside(text, start)
        if strings:
            inner = re.sub('""|`"', '"', inner)
        code = _powershell_escapes(inner, braces, commas, assigned, choices, places)
        return "$(" + code + ("\n)" if "#" in code.rsplit("\n", 1)[-1] else ")"), stop

    def closed(mark: str) -> None:
        """The string ends here. An empty one that is a word of its own is not handed on at
        all (`--target-org '' other` reaches the program as `--target-org other`)."""
        nonlocal joined
        if len(out) == opened + 1 and fresh \
                and command[i + 1:i + 2] in ("", " ", "\t", "\r", "\n", ";", "|", "&", ")", "}"):
            del out[opened:]
            return
        out.append(mark + (_PS_NATIVE if risky or last == "\\" or (odd and not spaced) else ""))
        if not fresh:
            joined = len(out)           # a string inside a word: the word goes on after it (`x"a"#b`)
        elif command[i + 1:i + 2] not in ("", " ", "\t", "\r", "\n", ";", "|", "&", ")", "}", ","):
            # A word that begins with a quoted string ends at its closing quote: what follows
            # is a word of its own (`"x"--target-org other`).
            out.append(" ")

    def starts() -> bool:
        """A token starts here: nothing written before this place belongs to the same word."""
        return len(out) == apart or ((not out or out[-1][-1:] in _PS_TOKEN_START) and len(out) != joined)

    def own() -> bool:
        """Is the `#`, `<#`, `<` or `>` here PowerShell's own, and not a character of a word?"""
        nonlocal settled
        if starts():
            return True
        if settled:                     # the same word as a place read as part of it: one token, one answer
            return False
        places.append(i)
        settled = not (len(places) <= len(choices) and choices[len(places) - 1])
        return not settled

    while i < len(command):
        c, follower = command[i], command[i + 1:i + 2]
        if not quote and c == "\r":
            c = "\n"                    # CR alone also ends a PowerShell statement
        if quote:
            spaced = spaced or c in " \t\r\n"
            odd = odd or c in _PS_CMD_CHARS
        else:
            if nested and nested[-1] == "value" and c in ";\n":
                nested[-1] = "key"      # the next entry of the hash literal
                entry = len(out) + 1    # after this separator, which is copied below
            if c in _PS_TOKEN_START:
                settled = False
            if c in ";\n|{}(&":
                head = ""
            elif not head and c not in " \t\r":
                head = c
        if quote == "'":
            if c == "'" and follower == "'":
                out.append("'\\''")
                last = c
                i += 2
                continue
            if c == "'":
                quote = ""
                closed(c)
            else:
                risky = risky or c == '"'
                last = c
                out.append(c)
        elif c == "`" and follower:
            if follower in "\r\n":
                if not quote and starts():
                    i += 3 if command[i + 1:i + 3] == "\r\n" else 2
                    continue
                # Within a word or string the backtick quotes the line-end character.
                out.append(follower if quote else "'" + follower + "'")
                spaced = True
                joined, last = len(out), follower
                i += 2
                continue
            value = _PS_ESCAPE_VALUES.get(follower, follower)
            out.append(("'" + value + "'") if not quote and follower in _PS_ESCAPE_VALUES
                       else "\\" + follower if not quote or follower in '"$`\\' else value)
            spaced = spaced or value in " \t\r\n"
            if follower == '"':
                risky = True
                if not quote:
                    out.append(_PS_NATIVE)
            joined = len(out)           # an escaped character is part of a word, whatever it is (`` `;#b ``)
            last = value
            i += 2
            continue
        elif c == "$" and follower and follower in "$?^":
            # `$$`, `$?` and `$^` are whole variables: what follows them begins something
            # else (`"$${"` is the variable `$$` and a brace, not the start of `${...}`).
            out.append("${v}")
            joined = len(out)
            risky, last = risky or bool(quote), follower
            i += 2
            continue
        elif c == "$" and follower == "{":
            # `${any name}`: a backtick makes the next character part of the name, a closing
            # brace among them (`${a`}b}` is one variable, and ends at the second brace).
            stop = i + 2
            while stop < len(command) and command[stop] != "}":
                stop += 2 if command[stop] == "`" else 1
            out.append("${v}")
            joined = len(out)
            risky, last = risky or bool(quote), "}"
            i = min(stop + 1, len(command))
            continue
        elif not quote and c == "$" and follower and follower in "'\"":
            out.append("\\$")
        elif (not quote and command.startswith("--%", i) and starts()
              and command[i + 3:i + 4] in ("", " ", "\t", "\r", "\n", "|")):
            end = min((command.find(mark, i) for mark in "\r\n"
                       if command.find(mark, i) >= 0), default=len(command))
            stop, inside = end, False
            for at in range(i + 3, end):
                if command[at] == '"':
                    inside = not inside
                elif command[at] == "|" and not inside:
                    stop = at
                    break
            first = command.find("|", i, end)
            if 0 <= first < stop:
                # A pipe inside double quotes does not end it. The cautious reading, where it
                # does, is kept as lines of their own after the text.
                later.append(command[first + 1:end])
            verbatim = command[i + 3:stop].strip()
            out.append("--% " + ("'" + verbatim.replace("'", "'\\''") + "' " if verbatim else ""))
            i = stop
            continue
        elif braces and not quote and c in "{}":
            if c == "{":
                nested.append("key" if command[i - 1:i] == "@" else "block")
            elif nested:
                nested.pop()
            out.append(" ; ")
            entry = len(out)
        elif braces and not quote and c == "=" and nested and nested[-1] == "key":
            # In a hash literal the first `=` of an entry follows its key, whatever the key is
            # (`@{1=sf ...}`, `@{-1=...}`, `@{[int] 1 = ...}`): the value is a statement. A
            # later `=` in the same entry belongs to the value (`--target-org=dev`).
            key = "".join(out[entry:]).strip()
            if re.fullmatch(r"[A-Za-z_]\w*", key):
                out[entry:] = [" '" + key + "' "]       # a bare name is text, never a command
            nested[-1] = "value"
            head = ""
            out.append(" ; ")
        elif not quote and c == "=" and head in ("$", "["):
            # An assignment: the statement begins with a variable or a type, whatever the
            # target looks like after that (`$1`, `$a.'b'`, `$a['x]y']`, `[int]$n`, `$a, $b`).
            # What stands on its right is a statement, and may be another assignment.
            head = ""
            out.append(" ; ")
            if assigned is not None:
                assigned.append(command[max(0, i - 24):i].strip())
        elif not quote and c == ",":
            # A comma with a space beside it makes an array, and a native program gets its
            # items as words of their own: `-q x, -o, prod` reaches sf as `-q x -o prod`.
            # A comma at a line's end continues the array on the next line.
            after = _PS_AFTER_COMMA.match(command, i + 1)
            if commas or after.end() > i + 1 or not out or out[-1][-1:] in " \t\r\n":
                out.append(" ")
                i = after.end()
                continue
            out.append(c)
        elif c == "\\":
            out.append("\\\\")
            last = c
        elif quote and c == '"' and follower == '"':
            out.append('\\"')
            risky, last = True, c
            i += 2
            continue
        elif not quote and c == "<" and follower == "#":
            if own():
                stop = command.find("#>", i + 2)
                out.append(" ")
                i = len(command) if stop < 0 else stop + 2
            else:                       # two characters of a word
                out.append("\\<#")
                i += 2
            continue
        elif not quote and (c in "<>" or (c in "*123456" and follower == ">" and starts())):
            # A redirection is no word of the command (`-o *>&1 org` names org), and what
            # follows it is a token of its own (`2>&1#x` ends in a comment). `*>` is written
            # as the plain redirection it is.
            if c not in "<>" or own():
                found = _PS_REDIRECTION.match(command, i)
                out.append(found.group().lstrip("*"))
                apart = len(out)
                i = found.end()
                end = None if "&" in found.group() else _ps_target(command, i, choices, places)
                if end is not None:
                    target = command[i:end].strip()
                    out.append(" 'torque-redirection-target' ")
                    ways, beyond = _powershell_ways("echo " + target, braces, commas, assigned)
                    targets += ways
                    if beyond:
                        places += [i] * (_PS_EACH_WAY + 1)
                    i = end
            else:                       # a character of a word
                out.append("\\" + c)
                i += 1
            continue
        elif not quote and c == "#" and own():
            # A line comment: its text is copied as it stands (a quote in it opens nothing),
            # after a space, since for Bash a comment begins only where a word would.
            stop = min((command.find(mark, i) for mark in "\r\n"
                        if command.find(mark, i) >= 0), default=len(command))
            out.append((" " if out and out[-1][-1:] not in " \t\r\n" else "") + command[i:stop])
            i = stop
            continue
        elif not quote and c == "@" and starts() and _PS_HERE.match(command, i):
            # (in the middle of a word `@"` is an `@` and an ordinary string: `x@"`)
            opener = _PS_HERE.match(command, i)
            mark = opener.group(1)
            closing = _ps_here_end(command, opener)
            body = command[opener.end():len(command) if closing is None else closing.start()]
            native = _PS_NATIVE if '"' in body or body.endswith("\\") or (mark == '"' and "$" in body) else ""
            if mark == "'":
                out.append("'" + body.replace("'", "'\\''") + "'" + native)
            else:       # an expandable string: its variables and $(...) still run
                text, j = [], 0
                while j < len(body):
                    if body[j] == "`" and j + 1 < len(body):
                        value = _PS_ESCAPE_VALUES.get(body[j + 1], body[j + 1])
                        text.append("\\" + body[j + 1] if body[j + 1] in '"$`' else value)
                        j += 2
                        continue
                    if body.startswith("$(", j):
                        piece, j = subexpression(body, j + 2, False)
                        text.append(piece)
                        continue
                    text.append({"\\": "\\\\", '"': '\\"'}.get(body[j], body[j]))
                    j += 1
                out.append('"' + "".join(text) + '"' + native)
            if (closing and command[closing.end():closing.end() + 1]
                    not in ("", " ", "\t", "\r", "\n", ";", "|", "&", ")", "}", ",")):
                out.append(" ")         # a suffix after the closing mark is another token
            apart = len(out)            # the here-string is a token: what follows its end is another
            i = len(command) if closing is None else closing.end()
            continue
        elif not quote and c in "'\"":
            quote, risky, spaced, odd, last, opened = c, False, False, False, "", len(out)
            fresh = starts()
            out.append(c)
        elif quote and c == '"':
            quote = ""
            closed(c)
        elif quote and c == "$" and follower == "(":
            # "... $( ... ) ...": inside the parentheses PowerShell reads code again, with
            # quotes of its own. A `"` there opens a string; it does not end this one. The
            # string now holds a value that is not on the line.
            text, i = subexpression(command, i + 2, True)
            out.append(text)
            risky, last = True, ")"
            continue
        elif not quote and c == "$" and follower == "(" and not starts() and (
                not re.search("[\"'`#]", _ps_inside(command, i + 2)[0]) or not own()):
            # `a$(...)#b`: a subexpression in the middle of a word is part of that word, and
            # the word goes on after its parenthesis. When what it holds could end elsewhere
            # for a token of its own (`-x$(...)` is a parameter and a parenthesis), this is
            # one more place that is read each way: the other way is the plain reading below.
            text, i = subexpression(command, i + 2, True)
            out.append(text)
            joined = len(out)
            continue
        else:
            if quote and c == "$" and follower and follower not in ' \t\r\n"':
                risky = True            # a variable (`$x`, `$1`, `$_`, `$?`): its value is not on the line
            last = c
            out.append(c)
        i += 1
    return ("".join(out) + "".join("\n" + _powershell_escapes(text, braces, commas, assigned) for text in later)
            + "".join("\n" + text for text in targets))


# `$x = `, `$env:NAME += `, `[int]$n = `, `$a, $b = `, `${x} = ` at the start of a statement:
# PowerShell runs the command on the right of it.
_PS_VARIABLE = r"(?:\[[\w.\[\], ]+\][ \t]*)?\$(?:[A-Za-z_][\w:.]*|\{(?:[^}`\n]|`.)*\})(?:\[[^\]\n]*\]|\.\w+)*"
_PS_ASSIGNMENT = re.compile(r"(?m)(^[ \t]*|[;{(|][ \t]*)" + _PS_VARIABLE + r"(?:(?:[ \t]*,[ \t]*|[ \t]+)" + _PS_VARIABLE
                            + r")*[ \t]*(?:[-+*/%]|\?\?)?=(?!=)[ \t]*")
# What else can stand before a command at the start of a statement: `return`, `throw` and
# `exit` run the pipeline after them, a dot runs a command in the current scope, `$x in`
# begins the list a `foreach` goes through, `name =` begins a value in a hash literal
# (a quoted name ends at its closing quote, not at an escaped one inside it).
_PS_BEFORE = re.compile(r"(?im)(^[ \t]*|[;{(|][ \t]*)(?:(?:return|throw|exit)\b[ \t]*|\.[ \t]+"
                        r"|\$(?:[\w:]+|\{(?:[^}`\n]|`.)*\})[ \t]+in[ \t]+"
                        r"|(?:[A-Za-z_]\w*|'[^'\n]*'|\"(?:[^\"\\\n]|\\.)*\")[ \t]*=(?!=)[ \t]*)")
# A line with none of these is plain words and plain quotes, which PowerShell and Bash
# read alike: a backtick, a comment, a character outside printable ASCII, a here-string,
# `--%`, `${`, a `$` before a quote, a doubled quote, a backslash before anything but a
# name, `((`, a `%` (a .cmd launcher fills in %NAME%).
_PS_OWN_QUOTING = re.compile(r"[`#%]|[^\x20-\x7e\t\r\n]|@[\"'][ \t]*\r?\n|<#|--%|\$[{'\"]|''|\"\"|\\(?![\w.~-])|\(\(")
# Anything else than words, spaces, plain quotes, a path and a statement separator: an
# assignment, a brace, a parenthesis, a variable, a pipe, a comma. On such a line the second
# net runs too: a statement form the readings do not know then ends in a question, not in
# a command taken for text.
_PS_NOT_PLAIN = re.compile(r"[^A-Za-z0-9 \t\r\n_./:;'\"\\-]")
_PS_NAMED = re.compile(r"(?i)(?<![\w\\/.:$-])(?:sf|sfdx|torque|jsc)(?:\.(?:exe|cmd|bat|ps1))?(?=[ \t])")


def _powershell_statements(text: str) -> str:
    """PowerShell text with what stands before a command at the start of a statement
    taken away (an assignment, `return`, a hash literal's `name =`), so that the
    command is read as one. The patterns look at the text with its quoted characters
    marked (_mask): a line that begins inside a string that holds a line end is no start
    of a statement, and a quote in a comment pairs with nothing (`echo "<newline>" ;
    sf ... #"="` lost its command to the pattern for a quoted name before `=`)."""
    text = _mask(text)
    while True:                         # each pass that changes the text shortens it
        shorter = _PS_BEFORE.sub(r"\1", _PS_ASSIGNMENT.sub(r"\1", text))
        if shorter == text:
            return text.translate(_UNMASK)
        text = shorter


# What a statement begins with when it runs nothing by itself: a quoted string, a
# variable, a type, an array or hash literal, a brace, a redirection, a comment.
# `-hostname`, `=hostname` and `!hostname` are commands, but unary expressions are not.
_PS_INERT_HEAD = frozenset("'\"$[@+{},<>#")
# These Bash builtins are not PowerShell commands. A literal call operator target still needs lookup.
_PS_BASH_BUILTINS = frozenset({"exit", "return", "export", "declare", "typeset", "readonly", "local",
                               "unset", "shift", "source", "alias", "unalias", "hash", "wait", "jobs",
                               "fg", "bg", "dirs", "read", "command", "builtin", "exec", "noglob",
                               "nocorrect", ":", "["})
_PS_NUMBER = re.compile(r"(?i)(?:0x[0-9a-f]+|\d+\.?\d*(?:e[+-]?\d+)?|\.\d+)(?:[dl]|[kmgtp]b)*\Z")
_PS_EXPRESSION_HEAD = re.compile(r"(?i)-(?:not|bnot|join|split)(?!\w)|[-!]+(?=[$'\"[@])")
_PS_STATEMENT_END = re.compile(r"([;\n|&()]+)")
# What stands directly before a `(` when that is a method call: a member after a dot or
# `::`, whatever names it (`$x.Invoke`, `[scriptblock]::Create`, `(...).Invoke`, `$x.'Invoke'`,
# `$x.$name`, `$x.$env:NAME`, `$x.${name}`, `$x."$name"`), or the dot alone when the name is
# computed (`$x.('In' + 'voke')()`). So: a dot or `::` anywhere in the word the parenthesis
# follows, a quoted piece of that word counting as part of it.
_PS_METHOD = re.compile(r"(?:\.|::)[ \t\r\n]*(?:\"[^\"]*\"|'[^']*'|\S)*"
                        r"(?:(?<=[\"'])[ \t]*)?\Z")
# The same call written with a script block for its argument and no parentheses
# (`$x.ForEach{ ... }`, `$x.Where{ ... }`): a member directly before a brace. A `${name}`
# or a hash literal there is not one.
_PS_METHOD_BLOCK = re.compile(r"(?:\.|::)[ \t\r\n]*(?:\"[^\"]*\"|'[^']*'|\$\{[^}\n]*\}|[^\s{}();|&])*"
                              r"(?<![$@.:])(?:(?<=[\"'])[ \t]*)?\{")
_PS_METHOD_NOTE = " (a method call: the gate cannot tell what it runs)"
# PowerShell's escaped dollar (`` `$5 ``), which is not the last of a pair of backticks.
_PS_ESCAPED_DOLLAR = re.compile(r"(?<!`)((?:``)*)`\$")
_PS_HIDDEN_NOTE = " (inside braces, where PowerShell runs it as a command)"
# A word that names one of PowerShell's own stores as a drive (`env:NAME`, `function:sf`, `alias:`,
# `Environment::NAME`). `$env:NAME` reads a variable and is not one of these.
_PS_DRIVE = re.compile(r"(?i)(?:^|[:=,(@\\])(?:(?:env|function|alias|variable):(?!\s)"
                       r"|(?:environment|function|alias|variable)::)")


def _powershell_drive(text: str) -> str:
    """The first word that names PowerShell's store of environment variables, functions,
    aliases or variables as a drive, or ''. Commands the gate takes for local change what
    is in them: `cp env:A env:B` sets a variable for the commands after it, `cp
    function:prompt function:sf` and `cd alias:; cp iex echo` replace a command (checked
    in real Windows PowerShell)."""
    words = _tokens(text)
    for word in (text.split() if words is None else words):
        if _PS_DRIVE.search(str(word)):
            return " ".join(str(word).split())[:40]
    return ""


def _powershell_hidden(text: str, depth: int) -> list[Route]:
    """What the gate would ask about on a line of its own, among the statements of a
    PowerShell text rewritten with every brace ending a statement (_powershell_escapes).
    PowerShell runs the value of a hash literal's entry as a statement before the command
    that gets the literal starts (`echo @{1=python x.py}` runs python), and many commands
    run a script block they are given (`select @{n='x';e={./x.cmd}}`). A statement that
    begins with a string, a number, a variable or a type runs nothing by itself, unless it
    calls a method: `$x.Invoke()`, `[scriptblock]::Create(...)` and
    `$ExecutionContext.InvokeCommand.InvokeScript((cat x.txt))` run code, also as an
    argument of a command (`echo $x.Invoke()`), so a method call (a member's name
    directly before a parenthesis) is asked about wherever it stands."""
    found = []
    # A line end after a member's dot or :: still goes on with that member, not another statement.
    marked = re.sub(r"((?:\.|::))[ \t]*(?:\r\n?|\n)[ \t\r\n]*", r"\1 ", _mask(text))
    pieces = _PS_STATEMENT_END.split(marked)
    for at in range(0, len(pieces), 2):
        words = pieces[at].split()
        if not words:
            continue
        if words[0].casefold() == "function" and len(words) > 1:
            # PowerShell function names are case insensitive, with or without a scope.
            name = words[1].translate(_UNMASK)
            scope, colon, head = str(name).partition(":")
            if ((not colon or scope.casefold() in ("global", "script", "local", "private"))
                    and g._basename(head if colon else name) in
                    {"sf", "sfdx", "torque", "jsc"} | LOCAL_COMMANDS | BENIGN_WRAPPERS):
                detail = ("function " if colon else "") + str(name)
                found.append(Route("unverifiable", None,
                                   detail + " (defines what this command name runs)"))
        if at + 1 < len(pieces) and pieces[at + 1].startswith("(") and _PS_METHOD.search(pieces[at]):
            found.append(Route("unverifiable", None,
                               words[-1].translate(_UNMASK)[-60:] + "(...)" + _PS_METHOD_NOTE))
        if (words[0][0] in _PS_INERT_HEAD or _PS_NUMBER.match(words[0].lstrip("-!"))
                or _PS_EXPRESSION_HEAD.match(words[0])):
            continue
        if words[0][0] in "-!=" and (not words[0].strip("-!=") or (at and pieces[at - 1].endswith(")"))):
            # A sign alone, or an operator after a parenthesis (`(Get-Date) -gt $d`, `-(1 + 2)`),
            # goes on with an expression: no command begins there.
            continue
        commands = classify_bash(pieces[at].translate(_UNMASK), depth + 1, _net=False)
        if words[0].casefold() in _PS_BASH_BUILTINS - {"exit", "return"} and not any(
                route.kind == "unverifiable" for route in commands):
            found.append(Route("unverifiable", None,
                               words[0].translate(_UNMASK) + " (a PowerShell command name, not a Bash builtin)"))
        found += [route for route in commands
                  if route.kind == "unverifiable" and not route.detail.endswith(_ADDS_WORDS_NOTE)]
    return found


def _powershell_ways(command: str, braces: bool = False, commas: bool = False,
                     assigned: list | None = None) -> tuple[list[str], bool]:
    """(the line rewritten in Bash's quoting (_powershell_escapes) once for each way its
    `#`, `<`, `>` and `$(` in the middle of a word can be read, the reading with each of
    them part of its word first; whether the line has more such places than the gate reads
    each way)."""
    texts, pending, more = [], [()], False
    while pending:
        choices = pending.pop(0)
        places: list = []
        texts.append(_powershell_escapes(command, braces, commas, assigned, choices, places))
        more = more or len(places) > _PS_EACH_WAY
        # (this reading took every place after its choices as part of a word: each other
        # reading differs from it at one of those places first)
        pending += [choices + (False,) * (at - len(choices)) + (True,)
                    for at in range(len(choices), min(len(places), _PS_EACH_WAY))]
    return list(dict.fromkeys(texts)), more


def _powershell_texts(command: str) -> tuple[str, list[str], bool]:
    """(the command with the quote, dash and space characters PowerShell accepts written
    as the ASCII ones; that text rewritten in Bash's quoting: as it stands, with every
    brace ending a statement and every comma separating words, and with the commas
    alone, each of them every way it reads (_powershell_ways); whether it reads more ways
    than those)."""
    plain = command.translate(_PS_CHARACTERS)
    texts, more = [], False
    for braces, commas in ((False, False), (True, True), (False, True)):
        ways, beyond = _powershell_ways(plain, braces, commas)
        texts += [_powershell_statements(text) for text in ways]
        more = more or beyond
    return plain, list(dict.fromkeys(texts)), more


def _powershell_routes(command: str, depth: int = 0) -> list[Route]:
    """The routes of a PowerShell command line: the plain reading, and what PowerShell's
    own rules add to it. Every reading has to pass.
    - The quote, dash and space characters PowerShell accepts beside the ASCII ones
      are read as those (_PS_CHARACTERS).
    - PowerShell does not join lines at a backslash: the line is read with each line
      on its own.
    - Its escapes, block comments, here-strings and stop-parsing token: the line is
      read rewritten in Bash's quoting (_powershell_escapes), once more with every
      brace ending a statement, and without what stands before a command at the start
      of a statement (_powershell_statements).
    - The strings it hands to something that runs them, and a decoded
      -EncodedCommand (_powershell_refusals).
    - A net under all of these: each line on its own, in a fresh quoting context,
      keeping the routes of commands the gate knows, so a quoting form these readers
      do not know cannot hide a command that starts a line.
    - A second net, for a line that is more than plain words and plain quotes (any
      quoting of PowerShell's own, an assignment, a brace, a variable, a pipe): where
      the text names sf or Torque and reading from that name gives a route no reading
      above found, the consultant is asked.
    A write found only by one of the added readings counts; the same write (to the
    same org) read again is not counted twice."""
    # Bash would take PowerShell's escaped dollar (`` "Paid `$5" ``) for the start of a
    # backtick substitution; it is a plain `$`, which Bash writes with a backslash.
    routes = classify_bash(_PS_ESCAPED_DOLLAR.sub(r"\1\\$", command), depth)
    added: list[Route] = []
    plain, readings, more = _powershell_texts(command)
    if any("\0" in text for text in readings):
        # A NUL ends the command line Windows hands to a native program.
        added.append(Route("admin", None, "a NUL in a PowerShell argument changes the native command line"))
    stands = [_powershell_statements(text) for text in _powershell_ways(plain)[0]]     # as it stands, each way
    if plain != command:
        added += classify_bash(plain, depth) + _powershell_refusals(command)
    apart = re.sub(r"\\(?=\r?\n)", "", plain)
    if apart != plain:
        added += classify_bash(apart, depth)
    for text in readings:
        added += classify_bash(text, depth)
    added += _powershell_refusals(plain)
    if g._PS_EVALUATOR_RE.search(plain):
        # Something here runs a string (Invoke-Expression, cmd /c, powershell -Command):
        # every quoted piece of more than one word is read as a command, from the same
        # quote-aware words as the rest.
        for word in dict.fromkeys(word for text in stands for word in _tokens(text) or []):
            if re.search(r"\S\s+\S", word) and "\n" not in word[:1]:
                added += [r for r in classify_bash(str(word).replace(_PS_NATIVE, ""), depth + 1) if r.kind != "local"]
    # The net: each line on its own, as written and as rewritten (a block comment on the
    # same line as a command is gone only in the rewritten one).
    # Each line is taken on its own before anything is stripped from it: a quote that the
    # readers pair wrongly must not reach into the next line.
    alone = re.sub(r"`\r?\n", " ", plain).splitlines()
    lines = [_powershell_statements(line) for line in alone] if len(alone) > 1 else []
    for line in dict.fromkeys([*lines,
                               *(_powershell_statements(text) for line in alone[:len(lines)]
                                 for text in _powershell_ways(line)[0]),
                               *(_powershell_statements(line) for text in readings for line in text.splitlines())]):
        added += [r for r in classify_bash(line, depth + 1) if r.kind in _NET_KINDS]
    written = {(r.kind, r.org) for r in routes if r.kind in ("org_write", "browser_write")}
    asked = []
    # The mark is the gate's own; what a route says about the command is the command's text.
    added = [replace(r, detail=r.detail.replace(_PS_NATIVE, "")) if _PS_NATIVE in r.detail else r for r in added]
    for route in added:
        if route.kind in _POWERSHELL_KINDS:
            routes.append(route)
        elif route.kind == "unverifiable" and route.detail.endswith(_ADDS_WORDS_NOTE):
            # A read with words PowerShell adds, or with an argument Windows PowerShell may
            # hand on as other words: the consultant is asked, as for `$MORE` in Bash.
            asked.append(route)
        elif route.kind in ("org_write", "browser_write") and (route.kind, route.org) not in written:
            # A write only a PowerShell reading finds. The same write read again (its
            # words can differ between readings) is not a second write; one in another
            # org is, and two writes are never one approved command.
            written.add((route.kind, route.org))
            routes.append(route)
    # What braces hold is run too: a command the gate would ask about on a line of its own
    # is asked about inside a hash literal or a script block, and so is an assignment
    # wherever it stands (`echo @{1=$env:NAME='...'}` sets a variable for the commands
    # after it, as `$env:NAME='...'` at the start of the line does). These stand beside a
    # write too: they are other statements than the write, as they would be on a line of their own.
    assigned: list = []
    braced = _powershell_ways(plain, True, True, assigned)[0]
    known = {route.detail for route in routes if route.kind == "unverifiable"}     # asked about already
    for text in dict.fromkeys(text for way in braced for text in (way, _powershell_statements(way))):
        hidden = [route if route.detail.endswith(_PS_METHOD_NOTE)
                  else replace(route, detail=route.detail + _PS_HIDDEN_NOTE)
                  for route in _powershell_hidden(text, depth) if route.detail not in known]
        routes += [route for route in hidden if route not in routes]
    if assigned:
        routes.append(Route("unverifiable", None, assigned[0] + "= (an assignment: what it sets can change what "
                                                                "the commands after it run)"))
    if more:
        routes.append(Route("unverifiable", None, "a `#`, `<`, `>` or `$(` in the middle of a word in more than "
                            f"{_PS_EACH_WAY} places (PowerShell takes each for part of the word or for a comment, "
                            "a redirection or a token of its own by where it stands; the gate reads only that "
                            "many each way)"))
    drive = next(filter(None, map(_powershell_drive, stands)), "")
    if drive:
        routes.append(Route("unverifiable", None, drive + " (names PowerShell's store of environment variables, "
                                                          "functions or aliases, which a copy or a move changes)"))
    block = next(filter(None, (_PS_METHOD_BLOCK.search(_mask(text)) for text in stands)), None)
    if block:
        routes.append(Route("unverifiable", None,
                            block.group().translate(_UNMASK)[-60:] + " ...}" + _PS_METHOD_NOTE))
    # The call operator makes its literal target a command name, even if Bash reads it as syntax.
    for text in (*stands, *braced):
        words = _tokens(text) or []
        for at, word in enumerate(words[:-1]):
            if _is_operator(word) and word == "&":
                head = words[at + 1]
                if str(head).casefold() in _KEYWORDS | _PS_BASH_BUILTINS or _ASSIGN_RE.match(head):
                    routes.append(Route("unverifiable", None,
                                        str(head) + " (a PowerShell command name, not shell syntax)"))
    # A call operator takes the command name from the parenthesized pipeline's output.
    if any(re.search(r"&[ \t\r\n]*\(", _mask(text)) for text in stands):
        routes.append(Route("unverifiable", None,
                            "& (...) (a computed command: the gate cannot tell what it runs)"))
    if not written:
        routes += asked         # beside a write nothing only asks: the write needs its approval
    if not written and (
            plain != command or _PS_OWN_QUOTING.search(plain) or _PS_NOT_PLAIN.search(plain)):
        # The second net. A write is left out: it needs an approval of this exact text.
        found = {(r.kind, r.org, r.data) for r in routes}
        for tail in dict.fromkeys(text[name.start():].split("\n", 1)[0]
                                  for text in stands for name in _PS_NAMED.finditer(text)):
            if any(r.kind in _NET_KINDS and (r.kind, r.org, r.data) not in found for r in classify_bash(tail, depth + 1)):
                routes.append(Route("unverifiable", None, " ".join(tail.split()[:5])
                                    + " (text that names a command, in PowerShell quoting the gate cannot be sure of)"))
                break
    return list(dict.fromkeys(routes))


def _powershell_refusals(command: str) -> list[Route]:
    """The refused routes and the reads in a PowerShell command, read the way the
    build-only scan reads it (gate._powershell_commands): its escapes and path
    separators, a decoded -EncodedCommand, and the strings it hands to something
    that runs them."""
    try:
        texts = g._powershell_commands(command)
    except ValueError as exc:
        return [Route("admin", None, f"a PowerShell command the gate could not read ({exc})")]
    return [route for text in texts for route in classify_bash(text)
            if route.kind in _POWERSHELL_KINDS or route.kind in ("org_write", "browser_write")]


def classify(tool_name: str, tool_input: dict) -> list[Route]:
    """Every route a tool call takes."""
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    routes: list[Route] = []
    if tool_name.startswith("mcp__") or tool_name in g.MCP_LIKE_TOOLS:
        routes.append(classify_mcp(tool_name, tool_input))
        for text in g._command_strings(tool_input):
            routes.extend(r for r in classify_bash(text) if r.kind != "local")
        return list(dict.fromkeys(routes))
    command = tool_input.get("command")
    if tool_name == "Bash" or (tool_name not in g.SAFE_TOOLS and isinstance(command, str)):
        if "powershell" in tool_name.casefold():
            return _powershell_routes(str(command or ""))
        return classify_bash(str(command or ""))
    return [Route("local", None, tool_name)]
