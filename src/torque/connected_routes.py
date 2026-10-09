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

from dataclasses import dataclass, replace
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
# The long options the gate decides by. Torque's parsers take a prefix of an option's
# name as that option (`--target other` is `--target-org other`, and the last one given
# wins), so a name cut short would let the command use another org, client, filter or
# mode than the one the gate read.
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
GUARDED_READS = {("counts",): "counts", ("fill",): "counts", ("config",): "config_records",
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
_PLAIN = {c: chr(0xE000 + i) for i, c in enumerate("$`{}*?[@();<>|&\n#")}
_FILLED = {c: chr(0xE100 + i) for i, c in enumerate("$`")}
# Inside a comment or a here-document's body the shell takes nothing as quoting: the
# quotes and backslashes there are marked, so the splitter does not pair them with real
# ones (`echo a # it's` followed by a line with another apostrophe).
_INERT = {c: chr(0xE200 + i) for i, c in enumerate("'\"\\")}
_UNMASK = {ord(mark): c for table in (_PLAIN, _FILLED, _INERT) for c, mark in table.items()}
_WORD_BREAK = frozenset(" \t\r\n;|&()<>")
def _heredoc(command: str, i: int) -> tuple[str, bool] | None:
    """(delimiter, leading tabs dropped) of the here-document the `<<` at `i` opens, or
    None when no word follows it. The delimiter is the word after `<<` with its quoting
    removed, as Bash takes it, whatever characters it holds: `<<E`, `<<-E`, `<< 'E F'`,
    `<<"E"`, `<<\\E`, `<<\\!`, `<<E"O"F`, `<<@E@`. Nothing in it is expanded."""
    j, size = i + 2, len(command)
    tabs = command[j:j + 1] == "-"
    j += tabs
    while command[j:j + 1] in (" ", "\t"):
        j += 1
    out, start = [], j
    while j < size and command[j] not in " \t\r\n;|&()<>":
        c = command[j]
        if c == "'":
            stop = command.find("'", j + 1)
            stop = size if stop < 0 else stop
            out.append(command[j + 1:stop])
            j = stop + 1
        elif c == '"':
            j += 1
            while j < size and command[j] != '"':
                if command[j] == "\\" and command[j + 1:j + 2] in ('"', "\\", "$", "`"):
                    j += 1
                out.append(command[j])
                j += 1
            j += 1
        elif c == "\\" and j + 1 < size:
            out.append(command[j + 1])
            j += 2
        else:
            out.append(c)
            j += 1
    return ("".join(out), bool(tabs)) if j > start else None
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
        if mark.startswith("${"):
            continue            # an expansion, kept for the net (_substitution_tails)
        stop = _substitution_end(command, i) if mark == "$(" else _closing(command, i + 1, "`")
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
            if mark.startswith("${"):
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


def _plain_word(text: str) -> str:
    """Text as one single-quoted piece for the splitter, every special character marked plain."""
    return "'" + "".join(_PLAIN.get(c, c) for c in text).replace("'", "'\\''") + "'"


def _inert(text: str) -> str:
    return "".join(_INERT.get(c, c) for c in text)


def _sealed(text: str) -> str:
    """Text inside a double-quoted word that the splitter must take whole: a command
    substitution, with quotes of its own."""
    return "".join(_INERT.get(c) or _PLAIN.get(c, c) for c in text)


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
            out.append(c)
            i += 1
        elif follower in _ANSI_C:
            out.append(_ANSI_C[follower])
            i += 2
        elif follower in "01234567":
            digits = re.match(r"[0-7]{1,3}", text[i + 1:]).group()
            out.append(chr(int(digits, 8) & 0xFF))
            i += 1 + len(digits)
        elif follower in "xuU" and re.match(r"[0-9A-Fa-f]", text[i + 2:i + 3]):
            digits = re.match(r"[0-9A-Fa-f]{1,%d}" % {"x": 2, "u": 4, "U": 8}[follower], text[i + 2:]).group()
            value = int(digits, 16)
            out.append(chr(value) if value < 0x110000 else "\ufffd")
            i += 2 + len(digits)
        elif follower == "c" and text[i + 2:i + 3]:
            out.append(chr(ord(text[i + 2].upper()) ^ 0x40))
            i += 3
        else:
            out.append(c + follower)
            i += 2
    return "".join(out).split("\0", 1)[0]


_CASE_WORD = re.compile(r"(case|esac)(?![A-Za-z0-9_./-])")


def _closing(command: str, j: int, close: str) -> int | None:
    """The index just past the `close` character that ends what starts at `j` (a `${`
    expansion, an ANSI-C string, a backtick span), skipping escaped characters."""
    while j < len(command) and command[j] != close:
        j += 2 if command[j] == "\\" else 1
    return j + 1 if j < len(command) else None


_PARAMETER_NAME = re.compile(r"[!#]?(?:[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[@*#?$!-])(?:\[[^\]\n]*\])?")


def _parameter_end(command: str, i: int, quoted: bool) -> int | None:
    """The index just past the `}` that closes the `${` at `i`, or None when it does not
    close. Bash reads the word inside the braces with quoting of its own, also when
    the expansion stands in double quotes (`"${x:-"'"}"` is one word): a backslash, a
    double-quoted piece (in which a `}` ends nothing), an ANSI-C string, another
    expansion, a command substitution, and single quotes. In a double-quoted
    expansion whose operator takes a word (`:-`, `:=`, `:+`, `:?`, with or without
    the colon) a single quote is an ordinary character; with a pattern operator
    (`#`, `%`, `/`) it quotes, as it does outside double quotes."""
    j, size = i + 2, len(command)
    name = _PARAMETER_NAME.match(command, j)
    operator = command[name.end() if name else j:][:2]
    plain_quote = quoted and (operator[:1] in ("-", "=", "+", "?") or (operator[:1] == ":" and operator[1:2] in
                                                                         ("-", "=", "+", "?")))
    inner = False           # inside a double-quoted piece of the word
    while j < size:
        c, follower = command[j], command[j + 1:j + 2]
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
            stop = _closing(command, j + 2, "'")
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


def _substitution_end(command: str, i: int) -> int | None:
    """The index just past the `)` that closes the `$(` at `i`, or None when it does
    not close. The substitution is read as Bash reads it: with quotes of its own, and
    with the places where a `)` does not end it: inside quotes, an ANSI-C string or a
    `${...}` expansion, after a backslash, in a comment, in the body of a
    here-document, and at the end of a `case` pattern."""
    depth, quote, j, size = 0, "", i + 1, len(command)
    word_start, cases, opened = False, [], []
    while j < size:
        c = command[j]
        if quote == "'":
            quote = "" if c == "'" else quote
            word_start = False
        elif c == "\\":
            j += 1
            word_start = False
        elif c == "$" and command[j + 1:j + 2] == "(":
            inner = _substitution_end(command, j)       # a substitution inside this one, quoted or not
            if inner is None:
                return None
            j, word_start = inner, False
            continue
        elif c == "$" and command[j + 1:j + 2] == "{":
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
        elif c == "$" and command[j + 1:j + 2] == "'":
            inner = _closing(command, j + 2, "'")
            if inner is None:
                return None
            j, word_start = inner, False
            continue
        elif word_start and _CASE_WORD.match(command, j):
            word = _CASE_WORD.match(command, j)
            if word.group(1) == "case":
                cases.append(depth)
            elif cases:
                cases.pop()
            j, word_start = word.end(), False
            continue
        else:
            if c == "<" and command.startswith("<<", j) and not command.startswith("<<<", j) \
                    and command[j - 1:j] != "<":
                found = _heredoc(command, j)
                if found:
                    opened.append(found)
            if c in "'\"":
                quote = c
            elif c == "(":
                depth += 1
            elif c == ")" and not (cases and cases[-1] == depth):       # not the end of a case pattern
                depth -= 1
                if depth == 0:
                    return j + 1
            word_start = c in _WORD_BREAK
        j += 1
    return None


def _mask(command: str) -> str:
    return _scan(command)[0]


_BODY_SUBSTITUTION = re.compile(r"(?<!\\)(\$\(|`)")
_FILE_DESCRIPTOR = re.compile(r"[0-9]+(?=[<>])")
_QUOTING = frozenset("'\"\\")


def _scan(command: str) -> tuple[str, list]:
    """(the command with its plain, filled-in and inert characters marked (see above),
    where its command substitutions start), from one pass, so both agree on what is
    quoted. The line is read the way Bash reads quoting: single and double quotes, the backslash, ANSI-C
    quoting, a comment from an unquoted `#` that begins a word to the line end (a
    backslash before that line end does not continue it), and the body of a
    here-document. What is in a comment or a body is still split into words: cmd.exe
    runs the words after a `#`, and a body can be a script handed to a shell."""
    out, quote, i, size = [], "", 0, len(command)
    word_start = True       # where an unquoted # begins a comment
    opened: list = []       # the here-documents opened on this line: (delimiter, leading tabs dropped)
    starts: list = []       # (index, "$(" or "`") of each substitution the shell would run
    in_backticks = False
    while i < size:
        c = command[i]
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
            out.append(c + _PLAIN.get(command[i], command[i]))
            word_start = False
        elif quote:
            word_start = False
            if c == "$" and command[i + 1:i + 2] == "{":
                # "${x:-"'"}": the word in the braces has quoting of its own, and the
                # expansion is part of this one word.
                stop = _parameter_end(command, i, True)
                if stop is None or _QUOTING & set(command[i + 2:stop - 1]):
                    starts.append((i, '${"'))
                    if stop is not None:
                        starts += [(i + 2 + found.start(), found.group())
                                   for found in _BODY_SUBSTITUTION.finditer(command[i + 2:stop - 1])]
                        out.append(_FILLED["$"] + _sealed(command[i + 1:stop]))
                        i = stop
                        continue
            if c == "$" and command[i + 1:i + 2] == "(":
                # "$(printf "x;echo ")": the substitution has quotes of its own, and its
                # output is part of this one word.
                starts.append((i, "$("))
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
            out.append(_FILLED.get(c) or _PLAIN.get(c, c))
        else:
            if c == "#" and word_start:
                stop = command.find("\n", i)
                stop = size if stop < 0 else stop
                out.append(_inert(command[i:stop]))
                i = stop
                continue
            if c == "\n" and opened:
                out.append(c)
                i += 1
                for delimiter, tabs in opened:
                    while i < size:
                        stop = command.find("\n", i)
                        stop = size if stop < 0 else stop
                        line = command[i:stop]
                        out.append(_inert(line) + command[stop:stop + 1])
                        # The shell expands a substitution in a body (unless its delimiter is quoted).
                        starts += [(i + found.start(), found.group()) for found in _BODY_SUBSTITUTION.finditer(line)]
                        i = min(stop + 1, size)
                        if (line.lstrip("\t") if tabs else line).rstrip("\r") == delimiter:
                            break
                opened, word_start = [], True
                continue
            if c == "<" and command.startswith("<<", i) and not command.startswith("<<<", i) \
                    and command[i - 1:i] != "<":
                found = _heredoc(command, i)
                if found:
                    opened.append(found)
            if c == "$" and command[i + 1:i + 2] == "{":
                # ${x:-"}"} or ${x:-'}'}: the same outside double quotes. The `$` stays
                # unmarked: the shell can turn this word into several.
                stop = _parameter_end(command, i, False)
                if stop is None or _QUOTING & set(command[i + 2:stop - 1]):
                    starts.append((i, "${"))
                    if stop is not None:
                        starts += [(i + 2 + found.start(), found.group())
                                   for found in _BODY_SUBSTITUTION.finditer(command[i + 2:stop - 1])]
                        out.append('$"' + _sealed(command[i + 1:stop]) + '"')
                        i, word_start = stop, False
                        continue
            if c == "$" and command[i + 1:i + 2] == "(":
                starts.append((i, "$("))
            if word_start and c in "0123456789" and _FILE_DESCRIPTOR.match(command, i):
                # A number directly before a redirection is a file descriptor (`2>&1`) and not
                # a word of the command. With a space between, it is a word: `--target-org 123
                # >&2` names the org 123.
                i = _FILE_DESCRIPTOR.match(command, i).end()
                continue
            if c == "`":
                if not in_backticks:
                    starts.append((i, "`"))
                in_backticks = not in_backticks
            if c == "$" and command[i + 1:i + 2] == "'":
                # $'...' outside quotes is ANSI-C quoting: its escapes are decoded and the
                # result is plain text. Inside double quotes the same characters are not.
                stop = i + 2
                while stop < size and command[stop] != "'":
                    stop += 2 if command[stop] == "\\" else 1
                if stop < size:
                    out.append(_plain_word(_ansi_c(command[i + 2:stop])))
                    i = stop + 1
                    word_start = False
                    continue
            if c == "$" and command[i + 1:i + 2] == '"':
                # $"..." is a string the shell may translate (locale quoting): with a message
                # catalogue in place its text is another. The `$` stays, so the word counts
                # as one the shell builds.
                out.append(c)
                i += 1
                word_start = False
                continue
            quote = c if c in "'\"" else ""
            out.append(c)
            word_start = c in _WORD_BREAK
        i += 1
    return "".join(out), starts


class _Word(str):
    """A shell word that remembers how it was written: `raw` is the word with its
    plain and filled-in characters still marked (_mask), so a `$`, a backtick, a
    brace or a glob character left in `raw` stood outside quotes. `paren` is set
    when an opening parenthesis follows the word directly: in PowerShell that is
    an argument computed at run time (`torque (Get-Content words.txt)`)."""
    paren = False

    def __new__(cls, raw: str):
        word = super().__new__(cls, raw.translate(_UNMASK))
        word.raw = raw
        return word


def _raw(tok: str) -> str:
    """The word as written. A word whose writing is not known (the command's quotes
    did not pair, or the gate put the word together) is read as unquoted."""
    return getattr(tok, "raw", tok)


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


def _tokens(command: str) -> list[str] | None:
    """Quote-aware words and operator tokens, or None when the quoting does not parse.
    Each is a _Word: a str that remembers how it was written."""
    lex = shlex.shlex(_mask(command), posix=True, punctuation_chars=_PUNCTUATION)
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
    return _is_operator(tok) and ("<" in tok or ">" in tok) and not (set(tok) & _SEPARATOR_CHARS)


def _split(command: str) -> tuple[list[list[str]], list[str]] | None:
    """(segments of command words without redirections, operator tokens seen)."""
    toks = _tokens(command)
    if toks is None:
        return None
    segments: list[list[str]] = [[]]
    operators: list[str] = []
    skip, backticks = False, 0
    for tok in toks:
        if skip:
            skip = False
            if not _is_operator(tok):
                continue
        if _is_operator(tok):
            operators.append(tok)
            if segments[-1] and (tok.startswith("(") or (tok.startswith("`") and backticks % 2 == 0)):
                # WORD (...) or WORD `...`: an argument computed at run time follows the word.
                segments[-1][-1].paren = True
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
        plain, readings = _powershell_texts(command)
        return all(_one_command(re.sub(r"\A\s*&\s*(?=\S)", "", text)) for text in (command, plain, *readings))
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


# The options with which `sf api request rest` can only be a GET, and whether each
# takes a value. Any other option (a body, a file, a flags folder, one this version
# does not know) makes the request a write.
REST_READ_OPTIONS = {"-o": True, "--target-org": True, "-X": True, "--method": True, "-H": True, "--header": True,
                     "--api-version": True, "-S": True, "--stream-to-file": True, "-i": False, "--include": False,
                     "--json": False}


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
    found, i = [], 0
    while i < len(rest):
        if rest[i].startswith("-"):
            name, attached, _ = rest[i].partition("=")
            i += 1 if attached or not REST_READ_OPTIONS.get(name) else 2
        else:
            found.append(rest[i])
            i += 1
    return found[3] if found[:3] == ["api", "request", "rest"] and len(found) == 4 else None


def _sf(rest: list[str], detail: str) -> Route:
    route = _sf_route(rest, detail)
    if route.kind in ("read", "check_only", "org_write", "no_org", "unverifiable") and _attached_short(rest):
        return Route("admin", route.org, detail + " (an option with its value attached, or short options written "
                                                  "together: write each option and its value as separate words)")
    return route


def _sf_route(rest: list[str], detail: str) -> Route:
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
        if len(words) == len(name) and set(words) == set(name) and name != ("apex", "tail", "log"):
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
        custom = any("apexrest" in _decoded(w).casefold() or "executeanonymous" in _decoded(w).casefold()
                     for w in rest)
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
_REST_METADATA = re.compile(
    r"^/services/data/?(v[\d.]+/?)?$|/sobjects/?$|/sobjects/[^/?]+/describe|/describe/?$|/limits/?$",
    re.IGNORECASE)
# Tooling API entities that hold schema, configuration or code. A query on any other
# entity (User, TraceFlag, ApexExecutionOverlayResult, test results) reads records.
TOOLING_SCHEMA_ENTITIES = frozenset(name.casefold() for name in (
    "ApexClass", "ApexTrigger", "ApexPage", "ApexComponent", "CustomField", "CustomObject", "CustomTab",
    "EntityDefinition", "FieldDefinition", "Flow", "FlowDefinition", "FlowDefinitionView", "Layout",
    "LightningComponentBundle", "PermissionSet", "Profile", "RecordType", "StaticResource", "ValidationRule",
    "WorkflowRule"))
# A field or relationship that names a person on those entities: who made or changed
# a component, who owns it, a user.
_TOOLING_PERSON = re.compile(r"(created|lastmodified)by(id)?$|^(owner|user|username|email)(id)?$", re.IGNORECASE)
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
    if re.search(r"(^|/)\.{1,2}(/|$)", text) or "\\" in text or "#" in text:
        # .../sobjects/User/describe/../005...: the client folds the dots away before it
        # sends the request, so the server gets the row, not the describe.
        return "records"
    if re.search(r"/sobjects/apexlog(/|$)", text, re.IGNORECASE):
        return "debug_logs"
    tooling = re.search(r"/tooling/query/?$", text, re.IGNORECASE)
    if tooling or re.search(r"/query/?$", text, re.IGNORECASE):
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
    if _REST_METADATA.search(text):
        return None
    return "records"


# The route owner for a command that names an internal initiative. Connected mode
# serves one client's work only, so an initiative is never the bound client.
INITIATIVE_OWNER = "initiative:"


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
        return Route("admin", None, detail + f" (`{short}` is an option's name cut short, which the command would "
                                             "still take as that option; write option names in full)")
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
    for i, word in enumerate(words[1:], 1):
        base = g._basename(word)
        if g._is_sf_token(word) or base in g.CONSOLE_SCRIPTS or base in SHELLS or base == "env":
            return i
        if g.PY_LAUNCHER_RE.match(base):
            return i
    return None


def _strip_benign(head: str, words: list[str]) -> list[str]:
    rest = words[1:]
    while rest and rest[0].startswith("-"):
        takes_value = (head == "nice" and rest[0] == "-n") or (head == "timeout" and rest[0] in ("-s", "-k",
                                                                                                "--signal",
                                                                                                "--kill-after")) \
            or (head == "exec" and re.fullmatch(r"-[cl]*a", rest[0]) is not None)      # exec -a NAME command
        rest = rest[2:] if takes_value else rest[1:]
    if head == "timeout" and rest:
        rest = rest[1:]
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
    if words and words[0] in ("for", "select", "case"):
        return [Route("local", None, " ".join(words[:5]))]
    while words and (words[0] in _KEYWORDS or _ASSIGN_RE.match(words[0])):
        had_assignment = had_assignment or bool(_ASSIGN_RE.match(words[0]))
        words = words[1:]
    if not words:
        return [] if not had_assignment else [Route("local", None, "")]
    routes = _segment_core(words, depth)
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
    return [route, Route("unverifiable", route.org, detail + _ADDS_WORDS_NOTE, route.client)]


def _torque_routes(words: list[str], rest: list[str], detail: str) -> list[Route]:
    route = _torque(rest, detail, paren=bool(getattr(words[-1], "paren", False)))
    if rest and rest[0] in TORQUE_DELEGATED:
        return _with_run_time_words(words, route, detail)
    return [route]


def _segment_core(words: list[str], depth: int) -> list[Route]:
    first = words[0]
    head, rest = g._basename(first), words[1:]
    detail = " ".join(words[:5])
    if "$" in first or "`" in first:
        return [Route("unverifiable", None, detail + " (command built at run time)")]
    if head == "env":
        i = 0
        while i < len(rest) and (rest[i].startswith("-") or _ASSIGN_RE.match(rest[i])):
            if rest[i] in ("-S", "--split-string") or rest[i].startswith("-S"):
                return [Route("unverifiable", None, detail)]
            i += 2 if rest[i] in ("-u", "--unset", "-C", "--chdir") else 1
        inner = rest[i:]
        if not inner:
            return [Route("local", None, detail)]
        assigned = any(_ASSIGN_RE.match(w) for w in rest[:i])
        routes = _segment(["X=1", *inner] if assigned else inner, depth)
        if any(w in ("-C", "--chdir") or w.startswith(("-C", "--chdir=")) for w in rest[:i]):
            # The command runs in another folder than the one the gate reads paths from.
            routes = [Route("unverifiable", None, detail + " (runs in another folder)"), *routes]
        return routes
    if g._is_sf_token(first):
        return _with_run_time_words(words, _sf(rest, detail), detail, launcher=True)
    if head in g.CONSOLE_SCRIPTS:
        if g.CONSOLE_SCRIPTS[head] == "torque":
            return _torque_routes(words, rest, detail)
        if head == "jsc":
            return _torque_routes(words, ["revert", *rest], detail)
        if rest[:1] in (["--help"], ["-h"], ["--version"]):
            return [Route("local", None, detail)]
        return [Route("org_write" if org_value(rest) else "unverifiable", org_value(rest), detail)]
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
        if module and module.startswith("jsc_revert"):
            return _torque_routes(words, ["revert", *rest[after:]], detail)
        if rest[:1] in (["--version"], ["-V"]):
            return [Route("local", None, detail)]
        return [Route("unverifiable", None, detail)]
    if head == "eval":
        return [Route("unverifiable", None, detail), *classify_bash(" ".join(rest), depth + 1)]
    if head in BENIGN_WRAPPERS:
        inner = _strip_benign(head, words)
        if head == "command" and rest[:1] in (["-v"], ["-V"]):
            return [Route("local", None, detail)]
        return _segment(inner, depth) if inner else [Route("local", None, detail)]
    if head in NETWORK or head in ("open", "xdg-open", "start"):
        if any(SF_HOSTS.search(w) for w in rest):
            return [Route("unverifiable", None, detail + " (reaches a Salesforce host)")]
        if head == "wget" and any(w.split("=", 1)[0] in ("--use-askpass", "-e", "--execute") or
                                  (w.startswith("-") and not w.startswith("--") and "e" in w[1:]) for w in rest):
            # wget starts the program `--use-askpass` names, and `-e` gives it a line of its
            # start-up file, which can name one.
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
    if head == "hash" and any(w.startswith("-") and "p" in w for w in rest):
        # `hash -p FILE NAME`: from here on the shell runs FILE for NAME (`hash -p ./x sf`).
        return [Route("unverifiable", None, detail + " (makes a name run another program)")]
    if head in LOCAL_COMMANDS and known_path:
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
# Torque are configured by, proxies and certificates.
_READ_NAMES = re.compile(r"(?i)(?:PATH|HOME|IFS|CDPATH|EXECIGNORE|GLOBIGNORE|POSIXLY_CORRECT|SHELLOPTS|BASHOPTS|"
                         r"BASH_XTRACEFD|TMPDIR|TEMP|TMP|DEBUG|PWDEBUG|USERPROFILE|HOMEDRIVE|HOMEPATH|APPDATA|"
                         r"LOCALAPPDATA|COMSPEC|PATHEXT|(?:SF|SFDX|TORQUE|JSC|NODE|NPM|LD|DYLD|XDG|GIT|PYTHON|SSL|"
                         r"CURL)_\w*|\w*_PROXY)\Z")
_ASSIGNING_BUILTINS = frozenset({"read", "printf", "declare", "typeset", "local", "readonly", "export", "unset",
                                 "let", "getopts", "mapfile", "readarray"})
_NAME_AT_START = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(?:\+?=|\[|\Z)")


def _assigned_names(words: list[str]) -> list[str]:
    """The names a statement sets, or unsets, in the shell itself, so that the value is
    there for the commands after it: `NAME=value` with no command after it, the variable
    of a `for` or `select`, and the names given to read, printf -v, declare, typeset,
    local, readonly, export, unset, let, getopts, mapfile and readarray (after wrapper
    words such as command or builtin). A name the shell builds (`printf -v "$n"`) is given
    as `$`: it can be any name. A `NAME=value` before a command is set for that command
    alone, and is not one of these."""
    if words and words[0] in ("for", "select"):
        return [str(words[1])] if len(words) > 1 else []
    names = []
    while words:
        if g._basename(words[0]) in BENIGN_WRAPPERS:
            words = _strip_benign(g._basename(words[0]), words)
        elif words[0] in _KEYWORDS:
            words = words[1:]
        elif _ASSIGN_RE.match(words[0]):
            names.append(words[0].split("=", 1)[0])
            words = words[1:]
        else:
            break
    if not words:
        return names
    head, rest = words[0], words[1:]
    if head not in _ASSIGNING_BUILTINS:
        return []
    if head == "printf":                # only `-v NAME` is a name; the other words are text
        given = [rest[i + 1] for i, word in enumerate(rest[:-1]) if word == "-v"]
    elif head == "read":                # the word after one of these options is its value, not a name
        given = [word for i, word in enumerate(rest)
                 if not word.startswith("-") and not (i and rest[i - 1] in ("-d", "-i", "-n", "-N", "-p", "-t", "-u"))]
    else:
        given = [word for word in rest if not word.startswith(("-", "+"))]
    names = []
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
    the environment; names compared without case, as Windows has them), or one of the
    names programs and the shell read (_READ_NAMES)."""
    return name == "$" or bool(_READ_NAMES.match(name)) or name.upper() in {key.upper() for key in os.environ}


def _exports(words: list[str]) -> bool:
    """The segment puts variables into the environment of later commands (after any
    keywords, assignments and wrapper words such as command, builtin or time): `export`,
    `declare -x`/`typeset -x`/`local -x`/`readonly -x` (flags combined or not),
    `set -a`/`set -o allexport` (every later assignment is exported), or `set -k`/`set
    -o keyword` (a `NAME=value` word anywhere in a later command is set for it)."""
    while words:
        if g._basename(words[0]) in BENIGN_WRAPPERS:
            # `command export ...`, `builtin declare -x ...`, `time -p export ...`
            words = _strip_benign(g._basename(words[0]), words)
        elif words[0] in _KEYWORDS or _ASSIGN_RE.match(words[0]):
            words = words[1:]
        else:
            break
    if not words:
        return False
    head, flags = words[0], [w for w in words[1:] if w.startswith(("-", "+")) and w not in ("-", "--")]
    if head == "export":
        return True
    if head in ("declare", "typeset", "local", "readonly"):
        return any(f.startswith("-") and "x" in f[1:] for f in flags)
    if head == "set":
        rest = words[1:]
        return any(f.startswith("-") and not f.startswith("--") and set("ak") & set(f[1:]) for f in flags) or any(
            rest[i] == "-o" and i + 1 < len(rest) and rest[i + 1] in ("allexport", "keyword") for i in range(len(rest)))
    return False


# What the net under the substitution reader keeps: the routes of commands the gate
# knows. A stray word of the tail that looks like no command is not asked about.
_NET_KINDS = (*REFUSED_KINDS, "admin", "read", "check_only", "org_write", "browser_write", "no_org")


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
        # which splits more, never less.
        segments = [g._without_redirections(toks) for toks, _ in g._segments_with_separators(text) if toks]
    else:
        segments = parsed[0]
    exported = ""
    for words in segments:
        found = _segment(words, _depth)
        if exported:
            # An exported variable (DEBUG=pw:api prints the session URL, PWDEBUG forces a
            # visible browser, SF_* can point an alias at another org) reaches every later
            # program of the shell, so the gate cannot check what they do. So does a plain
            # assignment to a variable that is exported already (`PATH=/x:$PATH; sf ...`
            # runs another sf, `HOME=/x; sf ...` reads other aliases; checked in real Bash).
            found = _asked_as_well(found, exported)
        routes.extend(found)
        names = _assigned_names(words)
        if _BASH_RUNS & set(names):
            routes.append(Route("unverifiable", None, " ".join(words[:5]) + " (Bash runs commands from this variable)"))
        if not exported and _exports(words):
            exported = " (after an exported variable)"
        elif not exported and any(_read_by_later_commands(name) for name in names):
            exported = " (after a variable that the commands after it read was set)"
    setting = _CONTAINER_SET_RE.search(text)
    if setting:
        # NAME=true sf org open, export NAME=..., $env:NAME = ...: with it set, an
        # approved `sf org open` prints its login URL instead of opening a browser.
        routes.append(Route("admin", None, f"setting {setting.group(1).upper()} makes `sf org open` print its "
                                           "login URL"))
    # What a substitution runs is a command too. The looser scan pairs parentheses
    # without reading quotes, so `"$(echo ")" ; sf data query ...)"` would end at the
    # quoted one: the substitutions are also found with their own quotes.
    for nested in dict.fromkeys([*g._direct_substitutions(text), *_substitutions(command or "")]):
        routes.extend(r for r in classify_bash(nested, _depth + 1) if r.kind != "local")
    if "\\\r\n" in (command or ""):
        # A backslash before CR LF. Bash on Linux and macOS takes the CR for an escaped
        # character and ends the command at the LF (the reading above); Git Bash on
        # Windows drops the CR and joins the lines (checked there). The gate cannot tell
        # which Bash reads the line, so both readings have to pass.
        routes.extend(r for r in classify_bash(command.replace("\\\r\n", ""), _depth + 1) if r.kind != "local")
    if _net:
        for tail in _substitution_tails(command or ""):
            routes.extend(r for r in classify_bash(tail, _depth + 1, _net=False) if r.kind in _NET_KINDS)
        if "<<" in (command or "") and "\n" in command:
            # The net under the here-document reader: each line on its own, in a fresh
            # quoting context. Wherever a body really ends, the command after it begins
            # a line. (A body's own lines are read as commands in any case.)
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
        if words & MCP_WRITE_WORDS or not words & MCP_READ_WORDS:
            return Route("org_write" if org else "no_org", org, tool_name)
        data = ("debug_logs" if words & {"log", "logs", "debug"} else
                "records" if words & MCP_RECORD_WORDS else None)
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


_PS_HERE = re.compile(r"@([\"'])[ \t]*\r?\n")
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
_PS_AFTER_COMMA = re.compile(r"[ \t]*(?:\r?\n[ \t]*)?")
# Put after a word that Windows PowerShell may not hand to a native program as the one
# word it is in PowerShell: an unquoted expansion, so the word counts as one the shell
# can turn into several (_adds_words, _built_at_run_time).
_PS_NATIVE = "${native}"
_PS_CMD_CHARS = frozenset("&|<>^")


def _powershell_escapes(command: str, braces: bool = False, commas: bool = False,
                        assigned: list | None = None) -> str:
    """A PowerShell line rewritten in Bash's quoting, so that its words split as
    PowerShell passes them: a backtick makes the next character plain (`` `" `` inside
    double quotes, `` `$ ``), a doubled quote inside quotes is one quote, a backtick at
    a line end joins the lines, a backslash is an ordinary character, a block comment
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
    spaces. A string with any of these gets the mark _PS_NATIVE after it."""
    out, quote, i = [], "", 0
    risky = spaced = odd = False        # of the string being read
    last = ""                           # its last character
    opened = 0                          # where it began in `out`
    fresh = False                       # it began a word
    nested: list = []                   # the open braces: "block", or where a hash literal's entry is ("key", "value")
    subs: list = []                     # the open `"...$(`: [parentheses open inside it, the string's state]
    later: list = []                    # text to read once more as code, after a `--%`
    head = ""                           # the first character of the statement being read
    entry = 0                           # where the key of a hash literal's entry began in `out`

    def closed(mark: str) -> None:
        """The string ends here. An empty one that is a word of its own is not handed on at
        all (`--target-org '' other` reaches the program as `--target-org other`)."""
        before = out[opened - 1][-1:] if opened else ""
        if len(out) == opened + 1 and (not opened or before in _PS_TOKEN_START) \
                and command[i + 1:i + 2] in ("", " ", "\t", "\r", "\n", ";", "|", "&", ")", "}"):
            del out[opened:]
            return
        out.append(mark + (_PS_NATIVE if risky or last == "\\" or (odd and not spaced) else ""))
        if fresh and command[i + 1:i + 2] not in ("", " ", "\t", "\r", "\n", ";", "|", "&", ")", "}", ","):
            # A word that begins with a quoted string ends at its closing quote: what follows
            # is a word of its own (`"x"--target-org other`).
            out.append(" ")

    while i < len(command):
        c, follower = command[i], command[i + 1:i + 2]
        if quote:
            spaced = spaced or c in " \t\r\n"
            odd = odd or c in _PS_CMD_CHARS
        else:
            if nested and nested[-1] == "value" and c in ";\n":
                nested[-1] = "key"      # the next entry of the hash literal
                entry = len(out) + 1    # after this separator, which is copied below
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
            if follower == "\n" or command[i + 1:i + 3] == "\r\n":
                i += 2 if follower == "\n" else 3
                continue
            out.append("\\" + follower if not quote or follower in '"$`\\' else follower)
            if follower == '"':
                risky = True
                if not quote:
                    out.append(_PS_NATIVE)
            last = follower
            i += 2
            continue
        elif c == "$" and follower == "{":
            stop = command.find("}", i + 2)
            out.append("${v}")
            risky, last = risky or bool(quote), "}"
            i = len(command) if stop < 0 else stop + 1
            continue
        elif not quote and c == "$" and follower and follower in "'\"":
            out.append("\\$")
        elif (not quote and command.startswith("--%", i) and (not out or out[-1][-1:] in _PS_TOKEN_START)
              and command[i + 3:i + 4] in ("", " ", "\t", "\r", "\n", "|")):
            end = command.find("\n", i)
            end = len(command) if end < 0 else end
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
            stop = command.find("#>", i + 2)
            out.append(" ")
            i = len(command) if stop < 0 else stop + 2
            continue
        elif not quote and c == "#" and (not out or out[-1][-1:] in _PS_TOKEN_START):
            # A line comment: its text is copied as it stands (a quote in it opens nothing).
            stop = command.find("\n", i)
            stop = len(command) if stop < 0 else stop
            out.append(command[i:stop])
            i = stop
            continue
        elif not quote and c == "@" and _PS_HERE.match(command, i):
            opener = _PS_HERE.match(command, i)
            mark = opener.group(1)
            stop = command.find("\n" + mark + "@", opener.end() - 1)
            body = command[opener.end():len(command) if stop < 0 else stop]
            native = _PS_NATIVE if '"' in body or body.endswith("\\") or (mark == '"' and "$" in body) else ""
            if mark == "'":
                out.append("'" + body.replace("'", "'\\''") + "'" + native)
            else:       # an expandable string: its variables and $(...) still run
                text, j = [], 0
                while j < len(body):
                    if body[j] == "`" and j + 1 < len(body):
                        text.append("\\" + body[j + 1] if body[j + 1] in '"$`' else body[j + 1])
                        j += 2
                        continue
                    text.append({"\\": "\\\\", '"': '\\"'}.get(body[j], body[j]))
                    j += 1
                out.append('"' + "".join(text) + '"' + native)
            i = len(command) if stop < 0 else stop + 3
            continue
        elif not quote and c in "'\"":
            quote, risky, spaced, odd, last, opened = c, False, False, False, "", len(out)
            fresh = not out or out[-1][-1:] in _PS_TOKEN_START
            out.append(c)
        elif quote and c == '"':
            quote = ""
            closed(c)
        elif quote and c == "$" and follower == "(":
            # "... $( ... ) ...": inside the parentheses PowerShell reads code again, with
            # quotes of its own. A `"` there opens a string; it does not end this one.
            subs.append([0, (spaced, odd, opened, fresh)])
            quote = ""
            out.append("$(")
            i += 2
            continue
        elif not quote and subs and c == "(":
            subs[-1][0] += 1
            out.append(c)
        elif not quote and subs and c == ")":
            if subs[-1][0]:
                subs[-1][0] -= 1
            else:                       # back in the string, which now holds a value that is not on the line
                spaced, odd, opened, fresh = subs.pop()[1]
                quote, risky, last = '"', True, c
            out.append(c)
        else:
            if quote and c == "$" and follower and follower not in ' \t\r\n"':
                risky = True            # a variable (`$x`, `$1`, `$_`, `$?`): its value is not on the line
            last = c
            out.append(c)
        i += 1
    return "".join(out) + "".join("\n" + _powershell_escapes(text, braces, commas, assigned) for text in later)


# `$x = `, `$env:NAME += `, `[int]$n = `, `$a, $b = `, `${x} = ` at the start of a statement:
# PowerShell runs the command on the right of it.
_PS_VARIABLE = r"(?:\[[\w.\[\], ]+\][ \t]*)?\$(?:[A-Za-z_][\w:.]*|\{[^}\n]*\})(?:\[[^\]\n]*\]|\.\w+)*"
_PS_ASSIGNMENT = re.compile(r"(?m)(^[ \t]*|[;{(|][ \t]*)" + _PS_VARIABLE + r"(?:(?:[ \t]*,[ \t]*|[ \t]+)" + _PS_VARIABLE
                            + r")*[ \t]*(?:[-+*/%]|\?\?)?=(?!=)[ \t]*")
# What else can stand before a command at the start of a statement: `return`, `throw` and
# `exit` run the pipeline after them, a dot runs a command in the current scope, `$x in`
# begins the list a `foreach` goes through, `name =` begins a value in a hash literal.
_PS_BEFORE = re.compile(r"(?im)(^[ \t]*|[;{(|][ \t]*)(?:(?:return|throw|exit)\b[ \t]*|\.[ \t]+"
                        r"|\$(?:[\w:]+|\{[^}\n]*\})[ \t]+in[ \t]+"
                        r"|(?:[A-Za-z_]\w*|'[^'\n]*'|\"[^\"\n]*\")[ \t]*=(?!=)[ \t]*)")
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
    command is read as one."""
    while True:                         # each pass that changes the text shortens it
        shorter = _PS_BEFORE.sub(r"\1", _PS_ASSIGNMENT.sub(r"\1", text))
        if shorter == text:
            return text
        text = shorter


# What a statement begins with when it runs nothing by itself: a quoted string, a
# variable, a type, an array or hash literal, a sign, a brace, a redirection, a comment.
_PS_INERT_HEAD = frozenset("'\"$[@-+!{}=,<>#")
_PS_NUMBER = re.compile(r"(?i)(?:0x[0-9a-f]+|\d+\.?\d*(?:e[+-]?\d+)?|\.\d+)(?:[dl]|[kmgtp]b)*\Z")
_PS_STATEMENT_END = re.compile(r"([;\n|&()]+)")
# What stands directly before a `(` when that is a method call: a member after a dot or
# `::`, whatever names it (`$x.Invoke`, `[scriptblock]::Create`, `(...).Invoke`, `$x.'Invoke'`,
# `$x.$name`, `$x.$env:NAME`, `$x.${name}`, `$x."$name"`), or the dot alone when the name is
# computed (`$x.('In' + 'voke')()`). So: a dot or `::` anywhere in the word the parenthesis
# follows, a quoted piece of that word counting as part of it.
_PS_METHOD = re.compile(r"(?:\.|::)(?:\"[^\"]*\"|'[^']*'|\S)*\Z")
# The same call written with a script block for its argument and no parentheses
# (`$x.ForEach{ ... }`, `$x.Where{ ... }`): a member directly before a brace. A `${name}`
# or a hash literal there is not one.
_PS_METHOD_BLOCK = re.compile(r"(?:\.|::)(?:\"[^\"]*\"|'[^']*'|[^\s{}();|&])*(?<![$@.:])\{")
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
    pieces = _PS_STATEMENT_END.split(_mask(text))
    for at in range(0, len(pieces), 2):
        words = pieces[at].split()
        if not words:
            continue
        if at + 1 < len(pieces) and pieces[at + 1].startswith("(") and _PS_METHOD.search(pieces[at]):
            found.append(Route("unverifiable", None,
                               words[-1].translate(_UNMASK)[-60:] + "(...)" + _PS_METHOD_NOTE))
        if words[0][0] in _PS_INERT_HEAD or _PS_NUMBER.match(words[0]):
            continue
        found += [route for route in classify_bash(pieces[at].translate(_UNMASK), depth + 1, _net=False)
                  if route.kind == "unverifiable" and not route.detail.endswith(_ADDS_WORDS_NOTE)]
    return found


def _powershell_texts(command: str) -> tuple[str, list[str]]:
    """(the command with the quote, dash and space characters PowerShell accepts written
    as the ASCII ones, that text rewritten in Bash's quoting: as it stands, with every
    brace ending a statement and every comma separating words, and with the commas
    alone)."""
    plain = command.translate(_PS_CHARACTERS)
    return plain, list(dict.fromkeys(_powershell_statements(_powershell_escapes(plain, braces, commas))
                                     for braces, commas in ((False, False), (True, True), (False, True))))


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
    plain, readings = _powershell_texts(command)
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
        for word in _tokens(readings[0]) or []:
            if re.search(r"\S\s+\S", word) and "\n" not in word[:1]:
                added += [r for r in classify_bash(str(word).replace(_PS_NATIVE, ""), depth + 1) if r.kind != "local"]
    # The net: each line on its own, as written and as rewritten (a block comment on the
    # same line as a command is gone only in the rewritten one).
    lines = _powershell_statements(re.sub(r"`\r?\n", " ", plain)).splitlines()
    for line in dict.fromkeys([*(lines if len(lines) > 1 else []), *(line for text in readings for line in text.splitlines())]):
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
    braced = _powershell_escapes(plain, True, True, assigned)
    known = {route.detail for route in routes if route.kind == "unverifiable"}     # asked about already
    for text in dict.fromkeys((braced, _powershell_statements(braced))):
        routes += [route if route.detail.endswith(_PS_METHOD_NOTE)
                   else replace(route, detail=route.detail + _PS_HIDDEN_NOTE)
                   for route in _powershell_hidden(text, depth) if route.detail not in known]
    if assigned:
        routes.append(Route("unverifiable", None, assigned[0] + "= (an assignment: what it sets can change what "
                                                                "the commands after it run)"))
    drive = _powershell_drive(readings[0])
    if drive:
        routes.append(Route("unverifiable", None, drive + " (names PowerShell's store of environment variables, "
                                                          "functions or aliases, which a copy or a move changes)"))
    block = _PS_METHOD_BLOCK.search(_mask(readings[0]))
    if block:
        routes.append(Route("unverifiable", None,
                            block.group().translate(_UNMASK)[-60:] + " ...}" + _PS_METHOD_NOTE))
    if not written:
        routes += asked         # beside a write nothing only asks: the write needs its approval
    if not written and (
            plain != command or _PS_OWN_QUOTING.search(plain) or _PS_NOT_PLAIN.search(plain)):
        # The second net. A write is left out: it needs an approval of this exact text.
        found = {(r.kind, r.org, r.data) for r in routes}
        for name in _PS_NAMED.finditer(readings[0]):
            tail = readings[0][name.start():].split("\n", 1)[0]
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
