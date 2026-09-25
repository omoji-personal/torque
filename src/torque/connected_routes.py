"""Classify one tool call into routes for connected mode.

Fail closed: an unknown Salesforce CLI verb, an unknown Salesforce MCP tool and
an unknown browser action count as writes, and a program this module does not
recognize counts as unverifiable (the host asks the consultant). Parsing is
quote-aware, so a SOQL string with parentheses or semicolons stays one argument;
command substitutions, `bash -c` strings and wrapped commands are classified too.
See docs/connected-approval.md for what this cannot see."""
from __future__ import annotations

from dataclasses import dataclass
import re
import shlex

from . import gate as g

# Route kinds, from least to most restricted in the gate.
KINDS = ("local", "read", "check_only", "org_write", "browser_write", "unverifiable", "admin", "no_org")

# sf commands that only read, by their leading topic words (docs/connected-approval.md,
# "Host facts verified"). Everything else with an org flag is a write.
SF_READ = {
    ("data", "query"), ("data", "get", "record"), ("data", "search"), ("data", "export"),
    ("data", "bulk", "results"), ("data", "resume"),  # resume reports a job and can return its rows
    ("sobject", "describe"), ("sobject", "list"),
    ("org", "display"), ("org", "list"),
    ("project", "retrieve", "start"), ("project", "retrieve", "preview"),
    ("project", "deploy", "report"), ("project", "deploy", "preview"),
    ("apex", "get", "log"), ("apex", "list", "log"), ("apex", "get", "test"),
    ("flow", "get", "test"), ("logic", "get", "test"),
    ("package", "installed", "list"), ("package", "install", "report"), ("package", "uninstall", "report"),
    ("package", "version", "list"), ("community", "list", "template"), ("limits", "api", "display"),
    ("cmdt", "generate", "fromorg"),
}
# Reads of record data and of debug logs, which the client's consent must cover.
SF_RECORD_READS = {("data", "query"), ("data", "get", "record"), ("data", "search"), ("data", "export"),
                   ("data", "bulk", "results"), ("data", "resume")}
SF_LOG_READS = {("apex", "get", "log"), ("apex", "list", "log")}
SF_CHECK_ONLY = {("project", "deploy", "validate"), ("apex", "run", "test"), ("flow", "run", "test"),
                 ("logic", "run", "test")}
# Local file generation and tooling; they take no org.
SF_LOCAL = {("project", "generate"), ("project", "convert"), ("project", "list", "ignored"),
            ("template", "generate"), ("schema", "generate"), ("cmdt", "generate"), ("lightning", "generate"),
            ("apex", "generate"), ("code-analyzer",), ("dev",), ("lightning", "dev"), ("agent", "generate"),
            ("version",), ("help",), ("commands",), ("whatsnew",), ("which",), ("search",), ("info",),
            ("doctor",), ("autocomplete",), ("alias", "list"), ("config", "list"), ("config", "get"),
            ("env", "list"), ("plugins",), ("plugins", "inspect")}
# Login and browser sessions: the gate cannot tell what they do.
SF_ASK = {("org", "login"), ("org", "logout"), ("org", "open"), ("plugins", "install"), ("plugins", "link"),
          ("plugins", "update"), ("plugins", "uninstall"), ("plugins", "reset"), ("update",)}
# Changing an alias or the default org would move an approved command to another org.
SF_ADMIN = {("alias", "set"), ("alias", "unset"), ("config", "set"), ("config", "unset")}
SFDX_RECORD_READS = {"force:data:soql:query"}
SFDX_LOG_READS = {"force:apex:log:get", "force:apex:log:list"}
SFDX_READ = {"force:data:soql:query", "force:source:retrieve", "force:mdapi:retrieve", "force:org:display",
             "force:org:list", "force:schema:sobject:describe", "force:schema:sobject:list",
             "force:apex:log:get", "force:apex:log:list"}
TORQUE_ORG_FLAGS = set(g.ORG_FLAGS) | {"--org"}
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
# Programs that only read or change local files. Their arguments are data, so an
# `sf` word inside them (grep sf, echo sf) is not a call.
LOCAL_COMMANDS = {
    "ls", "cat", "head", "tail", "wc", "grep", "egrep", "fgrep", "rg", "ag", "ack", "fd", "tree", "echo",
    "printf", "pwd", "cd", "pushd", "popd", "dirs", "mkdir", "rmdir", "touch", "cp", "mv", "rm", "ln", "chmod",
    "chown", "diff", "cmp", "comm", "sort", "uniq", "cut", "tr", "paste", "join", "fold", "fmt", "nl", "rev",
    "jq", "yq", "less", "more", "file", "stat", "du", "df", "date", "cal", "basename", "dirname", "realpath",
    "readlink", "which", "type", "whereis", "test", "[", "[[", "true", "false", ":", "tar", "zip", "unzip",
    "gzip", "gunzip", "zcat", "bzip2", "xz", "shasum", "sha1sum", "sha256sum", "md5", "md5sum", "cksum",
    "xmllint", "column", "tee", "sleep", "export", "unset", "set", "shift", "read", "exit", "return", "local",
    "declare", "typeset", "readonly", "alias", "unalias", "hash", "wait", "jobs", "fg", "bg", "kill", "ps",
    "top", "uptime", "whoami", "id", "hostname", "uname", "sw_vers", "env", "printenv", "locale", "tput",
    "clear", "history", "man", "info", "help", "git", "gh", "hg", "patch", "iconv", "base64", "xxd", "od",
    "hexdump", "strings", "split", "csplit", "mktemp", "truncate", "dd", "sed", "awk", "gawk", "mawk", "nawk",
    "find", "ditto", "rsync", "scp", "pbcopy", "pbpaste", "say",
    "ruff", "black", "isort", "mypy", "flake8", "pylint", "prettier", "eslint", "shellcheck", "tsc",
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
BROWSER_SERVER = re.compile(r"(chrome|playwright|puppeteer|browser|firefox|safari|webdriver|selenium)",
                            re.IGNORECASE)
DESKTOP_SERVER = re.compile(r"(computer[-_]?use|desktop|applescript|automation)", re.IGNORECASE)
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
    data: str | None = None  # "records" or "debug_logs" when the read needs that consent class


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


def _tokens(command: str) -> list[str] | None:
    """Quote-aware words and operator tokens, or None when the quoting does not parse."""
    lex = shlex.shlex(command.replace("\\\n", " "), posix=True, punctuation_chars=_PUNCTUATION)
    lex.whitespace_split = True
    lex.whitespace = " \t\r"
    lex.commenters = ""
    try:
        return list(lex)
    except ValueError:
        return None


def _is_operator(tok: str) -> bool:
    return bool(tok) and all(c in _PUNCTUATION for c in tok)


def _is_redirect(tok: str) -> bool:
    return _is_operator(tok) and ("<" in tok or ">" in tok) and not (set(tok) & _SEPARATOR_CHARS)


def _split(command: str) -> tuple[list[list[str]], list[str]] | None:
    """(segments of command words without redirections, operator tokens seen)."""
    toks = _tokens(command)
    if toks is None:
        return None
    segments: list[list[str]] = [[]]
    operators: list[str] = []
    skip = False
    for tok in toks:
        if skip:
            skip = False
            if not _is_operator(tok):
                continue
        if _is_operator(tok):
            operators.append(tok)
            if _is_redirect(tok):
                skip = True
                if segments[-1] and segments[-1][-1].isdigit():
                    segments[-1].pop()
            else:
                segments.append([])
            continue
        segments[-1].append(tok)
    return [s for s in segments if s], operators


def is_simple(command: str) -> bool:
    """One command: no chaining, pipes, redirection, background, substitution or newline."""
    if "`" in command or "$(" in command or "<(" in command or ">(" in command or "\n" in command:
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


def _sf(rest: list[str], detail: str) -> Route:
    topic = _topic(rest)
    orgs = org_values(rest, _sf_org_flags(topic))
    org = orgs[0] if len(orgs) == 1 else None
    if len(orgs) > 1:
        return Route("no_org", None, detail + " (names more than one org)")
    if not topic:
        return Route("local", None, detail) if not orgs else Route("org_write", org, detail)
    if ":" in topic[0]:
        if topic[0] in SFDX_READ:
            data = ("records" if topic[0] in SFDX_RECORD_READS else "debug_logs" if topic[0] in SFDX_LOG_READS
                    else None)
            return Route("read" if org else "no_org", org, detail, data=data)
        return Route("org_write" if org else "no_org", org, detail)
    if _match(topic, SF_ADMIN):
        return Route("admin", org, detail)
    if _match(topic, SF_ASK):
        return Route("unverifiable", org, detail)
    if _match(topic, SF_LOCAL) and not orgs and topic[:3] != ("cmdt", "generate", "fromorg"):
        if topic[0] == "plugins" and len(topic) > 1 and topic[1] != "inspect":
            return Route("unverifiable", None, detail)
        return Route("local", None, detail)
    if topic[:3] == ("api", "request", "rest"):
        methods = _flag_values(rest, {"-X", "--method"})
        # A custom Apex REST endpoint can change data on any method; a request file sets its own method.
        custom = any("apexrest" in _decoded(w).casefold() or "executeanonymous" in _decoded(w).casefold()
                     for w in rest)
        kind = "read" if (all(m.upper() == "GET" for m in methods) and not custom
                          and not _flag_values(rest, {"--body", "-b", "--file", "-f"})) else "org_write"
        path = topic[3] if len(topic) > 3 else ""
        return Route(kind if org else "no_org", org, detail, data=rest_data_class(path) if kind == "read" else None)
    if topic[:3] == ("project", "deploy", "start") and "--dry-run" in rest:
        return Route("check_only" if org else "no_org", org, detail)
    if topic[:2] == ("org", "display") and "--verbose" in rest:
        return Route("org_write" if org else "no_org", org, detail + " (prints credentials)")
    if _match(topic, SF_CHECK_ONLY):
        return Route("check_only" if org else "no_org", org, detail)
    if _match(topic, SF_READ):
        data = "records" if _match(topic, SF_RECORD_READS) else "debug_logs" if _match(topic, SF_LOG_READS) else None
        if topic[:2] == ("org", "list"):
            return Route("read" if org else "local", org, detail)
        return Route("read" if org else "no_org", org, detail, data=data)
    return Route("org_write" if org else "no_org", org, detail)


# REST paths that return schema or org information, not record data. Anything
# else under the API (sobject rows, query, search, composite, UI API, Connect) is
# record data; Apex logs are debug logs.
_REST_METADATA = re.compile(
    r"^/services/data/?(v[\d.]+/?)?$|/sobjects/?$|/sobjects/[^/?]+/describe|/describe/?$|"
    r"/limits/?$|/tooling/(sobjects|query)", re.IGNORECASE)


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
    path = full
    text = full.split("?", 1)[0] if "/tooling/query" not in full else full
    if re.search(r"apex\s*log", full, re.IGNORECASE):
        return "debug_logs"
    if _REST_METADATA.search(text):
        return None
    return "records"


def _strip_context(rest: list[str]) -> tuple[list[str], str | None]:
    out, client, i = [], None, 0
    while i < len(rest):
        tok = rest[i]
        name = tok.split("=", 1)[0]
        if name in ("--workspace", "--client"):
            value = tok.split("=", 1)[1] if "=" in tok else (rest[i + 1] if i + 1 < len(rest) else "")
            if name == "--client":
                client = value
            i += 1 if "=" in tok else 2
            continue
        out.append(tok)
        i += 1
    return out, client


def _torque(rest: list[str], detail: str) -> Route:
    if "--" in rest:
        rest = rest[:rest.index("--")]
    rest, client = _strip_context(rest)
    head = rest[0] if rest else ""
    sub = rest[1] if len(rest) > 1 else ""
    orgs = org_values(rest, TORQUE_ORG_FLAGS)
    org = orgs[0] if len(orgs) == 1 else None
    if len(orgs) > 1:
        return Route("no_org", None, detail + " (names more than one org)", client)
    if head == "launch" or (head == "workspace" and sub in ("ai-access", "delegate")):
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
    if head == "client" and sub == "list":
        # Lists every client's name and org: one client per session, so refused.
        return Route("local", None, detail, "*")
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
        return Route("read" if org else "local", org, detail, client,
                     data="debug_logs" if head == "logs" else None)
    if head in TORQUE_BROWSER_ROUTES:
        return Route("browser_write" if org else "local", org, detail, client)
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
                                                                                                "--kill-after"))
        rest = rest[2:] if takes_value else rest[1:]
    if head == "timeout" and rest:
        rest = rest[1:]
    return rest


def _awk_runs(words: list[str]) -> bool:
    return any(re.search(r"system\s*\(|\|\s*getline|print[^;]*\|\s*\"", w) for w in words[1:])


def _sed_runs(words: list[str]) -> bool:
    return any(re.search(r"(^|[;\n{]\s*)e(\s|$)|/[gpIiMm0-9]*e[gpIiMmw0-9]*\s*($|;)", w) for w in words[1:])


def _segment(words: list[str], depth: int) -> list[Route]:
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
        routes = [Route("unverifiable", r.org, r.detail + " (with a variable assignment)", r.client)
                  if r.kind in ("read", "check_only", "org_write", "browser_write") else r for r in routes]
    return routes


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
        return _segment(["X=1", *inner] if assigned else inner, depth)
    if g._is_sf_token(first):
        return [_sf(rest, detail)]
    if head in g.CONSOLE_SCRIPTS:
        if g.CONSOLE_SCRIPTS[head] == "torque":
            return [_torque(rest, detail)]
        if head == "jsc":
            return [_torque(["revert", *rest], detail)]
        if rest[:1] in (["--help"], ["-h"], ["--version"]):
            return [Route("local", None, detail)]
        return [Route("org_write" if org_value(rest) else "unverifiable", org_value(rest), detail)]
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
            return [_torque(rest[after:], detail)]
        if module and module.startswith("jsc_revert"):
            return [_torque(["revert", *rest[after:]], detail)]
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
        if head in NETWORK:
            return [Route("local", None, detail)]
    if head == "find" and any(w in ("-exec", "-execdir", "-ok", "-okdir") for w in rest):
        routes = [Route("unverifiable", None, detail)]
        for i, w in enumerate(rest):
            if w in ("-exec", "-execdir", "-ok", "-okdir") and i + 1 < len(rest):
                end = next((j for j in range(i + 1, len(rest)) if rest[j] in (";", "+", "\\;")), len(rest))
                routes += _segment(rest[i + 1:end], depth + 1)
        return routes
    if head in ("awk", "gawk", "mawk", "nawk") and _awk_runs(words):
        return [Route("unverifiable", None, detail + " (runs a command)")]
    if head == "sed" and _sed_runs(words):
        return [Route("unverifiable", None, detail + " (runs a command)")]
    if head == "git" and any(w.startswith("alias.") or "=!" in w or w.startswith("core.") for w in rest):
        return [Route("unverifiable", None, detail + " (git configuration that can run a command)")]
    known_path = re.search(r"[/\\]", first) is None
    if head in LOCAL_COMMANDS and known_path:
        return [Route("local", None, detail)]
    routes = [Route("unverifiable", None, detail)]
    start = _inner_start(words)
    if start is not None:
        routes += _segment(words[start:], depth + 1)
    return routes


def classify_bash(command: str, _depth: int = 0) -> list[Route]:
    """Routes for one shell command string, in order, without duplicates."""
    if _depth > 8:
        return [Route("unverifiable", None, "nesting too deep")]
    text = g._decode_ansi_c(command or "")
    parsed = _split(text)
    routes: list[Route] = []
    if parsed is None:
        # Unbalanced quoting (a heredoc body, for example): read it the naive way,
        # which splits more, never less.
        segments = [g._without_redirections(toks) for toks, _ in g._segments_with_separators(text) if toks]
    else:
        segments = parsed[0]
    for words in segments:
        routes.extend(_segment(words, _depth))
    for nested in g._direct_substitutions(text):
        routes.extend(r for r in classify_bash(nested, _depth + 1) if r.kind != "local")
    return list(dict.fromkeys(routes)) or [Route("local", None, "")]


def mcp_org(tool_input: dict) -> str | None:
    values = [tool_input[k] for k in MCP_ORG_KEYS if isinstance(tool_input.get(k), str) and tool_input[k]]
    values = list(dict.fromkeys(values))
    return values[0] if len(values) == 1 else None


def _words(name: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return [w for w in re.split(r"[^A-Za-z0-9]+", spaced.casefold()) if w]


def classify_mcp(tool_name: str, tool_input: dict) -> Route:
    parts = tool_name.split("__")
    server, tool = (parts[1] if len(parts) > 2 else ""), parts[-1]
    words = set(_words(tool))
    if DESKTOP_SERVER.search(server):
        action = str(tool_input.get("action") or "")
        if words <= BROWSER_READ_WORDS | {"cursor", "position", "zoom", "screenshot"} or \
                (tool == "computer" and action in COMPUTER_READ_ACTIONS):
            return Route("read", None, tool_name)
        return Route("admin", None, tool_name + " (desktop control can reach a terminal)")
    if BROWSER_SERVER.search(server):
        if tool == "computer":
            action = str(tool_input.get("action") or "")
            return Route("read" if action in COMPUTER_READ_ACTIONS else "browser_write", None, tool_name)
        return Route("read" if words and words <= BROWSER_READ_WORDS else "browser_write", None, tool_name)
    if g._mcp_reaches_salesforce(tool_name):
        org = mcp_org(tool_input)
        if words & MCP_WRITE_WORDS or not words & MCP_READ_WORDS:
            return Route("org_write" if org else "no_org", org, tool_name)
        data = ("debug_logs" if words & {"log", "logs", "debug"} else
                "records" if words & MCP_RECORD_WORDS else None)
        return Route("read" if org else "no_org", org, tool_name, data=data)
    return Route("local", None, tool_name)


def classify(tool_name: str, tool_input: dict) -> list[Route]:
    """Every route a tool call takes."""
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    routes: list[Route] = []
    if tool_name.startswith("mcp__"):
        routes.append(classify_mcp(tool_name, tool_input))
        for text in g._command_strings(tool_input):
            routes.extend(r for r in classify_bash(text) if r.kind != "local")
        return list(dict.fromkeys(routes))
    command = tool_input.get("command")
    if tool_name == "Bash" or (tool_name not in g.SAFE_TOOLS and isinstance(command, str)):
        return classify_bash(str(command or ""))
    return [Route("local", None, tool_name)]
