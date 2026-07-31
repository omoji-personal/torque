"""Shared shell + Salesforce-CLI analysis for the Torque gates.

ONE classifier, used by BOTH prod_write_gate and destructive_data_gate, so the two hooks can
never disagree about whether a command runs `sf` or which subcommand it is. Round-10 audit
(codex + Claude hostile-qa) showed the gates must decide "is this sf, and which subcommand"
from a single, shared, expansion-aware argv classifier — not from raw-text regex (which the
destructive gate still used) and not by resolving through wrappers (which was unsound).

Design law: FAIL CLOSED on any segment that could reach `sf` but whose command cannot be
statically resolved to the literal program `sf`/`sfdx`. Parameter indirection (`$S`),
command substitution (`$(...)`, backticks), process substitution (`<(...)`), unknown
runners (`caffeinate sf …`), wrappers (`nice -n 10 sf …`) and interpreters (`bash -c '…sf…'`)
are all denied rather than guessed — because this process performs no shell expansion.
"""
import os, re, shlex, hashlib
from pathlib import Path

TARGET_FLAGS = {"--target-org", "-o", "--targetusername", "-u"}
INTERPRETERS = {"bash", "sh", "zsh", "dash", "ksh", "fish", "eval", "source", "python",
                "python2", "python3", "perl", "ruby", "node", "deno", "awk", "xargs", "parallel"}

# sf/sfdx subcommands that only READ — no org authorization required. Everything not here is
# treated as a write. Broad prefixes that hide writes (package version create/promote, plugins
# install) were removed after audit T10-05 / codex #9.
SF_READS = {
    ("data", "query"), ("data", "search"), ("data", "export"), ("data", "resume"),
    ("sobject", "describe"), ("sobject", "list"),
    ("schema", "generate"), ("limits",), ("doctor",), ("version",), ("help",), ("which",),
    ("org", "display"), ("org", "list"), ("org", "open"), ("org", "login"), ("org", "logout"),
    ("apex", "list"), ("apex", "tail"), ("apex", "get"),
    ("project", "retrieve"), ("project", "generate"), ("project", "convert"),
    ("package", "installed"), ("config", "get"), ("config", "list"),
    ("alias",), ("autocomplete",), ("info",), ("community",), ("agent", "preview"),
}
SF_READ3 = {("package", "version", "list"), ("package", "version", "report"),
            ("package", "version", "displayancestry"), ("package", "version", "displaydependencies"),
            ("package", "version", "displaycreate"), ("schema", "generate", "field"),
            ("schema", "generate", "sobject"), ("schema", "generate", "platformevent"),
            ("schema", "generate", "tab")}
COLON_READ = ("force:schema", "force:org:display", "force:org:list", "force:org:open",
              "force:auth", "force:data:soql:query", "force:data:record:get",
              "force:data:tree:export", "force:mdapi:retrieve", "force:source:retrieve",
              "force:package:installed", "force:apex:log", "force:apex:class:list")

# A segment is "sf-suspicious" if it shows a sign of a Salesforce WRITE. Over-broad here is
# SAFE (it only ever triggers a deny on a non-literal-sf command). Keyed on target flags and
# write-shaped subcommand verbs — NOT on the literal token "sf" (which false-positives on grep).
SF_SUSPICIOUS = re.compile(
    r"(--target-org|--targetusername|(?<![\w-])-[ou](?![\w-])|--flags-dir|"
    r"\b(apex\s+run|data\s+(create|update|delete|upsert|import|delete\s+bulk)|"
    r"project\s+deploy|project\s+delete\s+source|org\s+(assign|create|delete|enable|disable)|"
    r"package\s+(version|install|uninstall|create|delete|promote)|"
    r"force:(data|apex|source|mdapi|package)|force:org:(create|delete)))", re.I)
# literal-sf matcher, used ONLY to sharpen interpreter-payload denials
SF_WORD = re.compile(r"(?:^|[\s'\";|&(`])s\s*f(?:dx)?(?:[\s'\";|&)`]|$)", re.I)


def strip_continuations(cmd: str) -> str:
    return re.sub(r"\\\n", "", cmd)                   # shell removes backslash-newline (codex #1)


def split_segments(cmd: str):
    """Split into simple-command strings on ; && || | & and newline, honoring quotes/backslash."""
    segs, buf = [], []
    i, n, quote = 0, len(cmd), None
    while i < n:
        c = cmd[i]
        if quote:
            buf.append(c)
            if quote == '"' and c == "\\" and i + 1 < n:
                buf.append(cmd[i + 1]); i += 2; continue
            if c == quote:
                quote = None
            i += 1; continue
        if c in ("'", '"'):
            quote = c; buf.append(c); i += 1; continue
        if c == "\\" and i + 1 < n:
            buf.append(c); buf.append(cmd[i + 1]); i += 2; continue
        if cmd[i:i + 2] in ("&&", "||"):
            segs.append("".join(buf)); buf = []; i += 2; continue
        if c in (";", "|", "&", "\n"):
            segs.append("".join(buf)); buf = []; i += 1; continue
        buf.append(c); i += 1
    if quote:
        return segs, False
    segs.append("".join(buf))
    return segs, True


def grouping_or_subst(cmd: str) -> bool:
    if "$(" in cmd or "`" in cmd or "<(" in cmd or ">(" in cmd:
        return True
    return bool(re.search(r"(?:^|[\s;&|])\(", cmd))    # subshell group


def anchor_ref(tok: str) -> bool:
    """Any reference to the trust anchor EXCEPT a read of the approved-apex copy. Substring
    based so it also catches `cd ~/.torque && cat secret` (audit R-02) and $HOME/.torque."""
    low = tok.lower()
    if ".torque" not in low:
        return False
    return "/approved/" not in low and not low.rstrip("/").endswith(".torque/approved")


def indirect(tok: str) -> bool:
    return any(c in tok for c in "$`{}")               # $VAR ${VAR} $(...) `...`


def _strip_assignments(argv):
    i = 0
    while i < len(argv) and re.match(r"^[A-Za-z_]\w*=", argv[i]):
        i += 1
    return argv[i:]


def _cut_ddash(args):
    return args[:args.index("--")] if "--" in args else args


def targets(sf_args):
    """Every org target on the command line (after cutting at --). Attached short -oVAL parsed
    so legit writes aren't spuriously denied (audit R-05)."""
    args = _cut_ddash(sf_args)
    out, i = [], 0
    while i < len(args):
        t = args[i]
        if t in TARGET_FLAGS and i + 1 < len(args):
            out.append(args[i + 1]); i += 2; continue
        matched = False
        for f in TARGET_FLAGS:
            if t.startswith(f + "="):
                out.append(t[len(f) + 1:]); matched = True; break
        if not matched and re.match(r"^-[ou]\S", t) and not t.startswith("--"):
            out.append(t[2:])
        i += 1
    return out


def has_flags_dir(sf_args):
    return any(a == "--flags-dir" or a.startswith("--flags-dir=") for a in _cut_ddash(sf_args))


def subcommand(sf_args):
    """The positional subcommand tuple (lowercased). Colon form → ('force:…',)."""
    pos = [a for a in _cut_ddash(sf_args) if not a.startswith("-")]
    return tuple(p.lower() for p in pos)


def is_read(sf_args) -> bool:
    pos = subcommand(sf_args)
    if not pos:
        return True
    first = pos[0]
    if ":" in first:
        return first.startswith(COLON_READ)
    if first == "package" and len(pos) >= 2 and pos[1] == "version":
        return pos[:3] in SF_READ3
    if first == "schema" and len(pos) >= 3:
        return pos[:3] in SF_READ3
    key1 = (first,)
    key2 = (first, pos[1]) if len(pos) > 1 else None
    return key1 in SF_READS or (key2 is not None and key2 in SF_READS)


def analyze_bash(cmd: str):
    """Returns {'deny': (reason, fingerprint)} to block the whole command, OR
    {'deny': None, 'writes': [sf_args, …], 'anchor_writes': bool}. `writes` are the args AFTER
    the sf/sfdx token for each DIRECT sf WRITE (reads filtered out). Fails closed throughout."""
    cmd = strip_continuations(cmd)
    segs, ok = split_segments(cmd)
    if not ok:
        return {"deny": ("unparseable command (unbalanced quotes)", "unparseable")}
    if grouping_or_subst(cmd) and SF_SUSPICIOUS.search(cmd):
        return {"deny": ("command grouping/substitution around a Salesforce operation — "
                         "not statically authorizable; inline the value", "substitution")}
    writes = []
    for seg in segs:
        seg = seg.strip()
        if not seg:
            continue
        try:
            argv = shlex.split(seg)
        except ValueError:
            if SF_SUSPICIOUS.search(seg) or SF_WORD.search(seg):
                return {"deny": ("unparseable segment carrying a Salesforce operation", "unparseable")}
            continue
        if not argv:
            continue
        for tok in argv:
            if anchor_ref(tok):
                return {"deny": ("reference to the trust anchor (~/.torque) — the signing "
                                 "secret and tokens are operator-only", "anchor-ref")}
        a = _strip_assignments(argv)
        if not a:
            continue
        base0 = os.path.basename(a[0]).lower()
        suspicious = bool(SF_SUSPICIOUS.search(seg))
        if base0 in ("sf", "sfdx"):
            sf_args = a[1:]
            if is_read(sf_args):
                continue
            writes.append(sf_args)
            continue
        if indirect(a[0]):
            return {"deny": ("indirect command invocation ($VAR/$()/backtick) cannot be "
                             "authorized — use a literal `sf`", "indirect-argv0")}
        if base0 in INTERPRETERS and (suspicious or SF_WORD.search(seg)):
            return {"deny": (f"Salesforce operation via interpreter/here-string ({base0}) — "
                             "not authorizable", "interp-sf")}
        if suspicious:
            return {"deny": ("Salesforce operation under an unrecognized wrapper/runner — "
                             "not authorizable; call `sf` directly", "wrapper-sf")}
        # a non-sf command with no Salesforce signal → not our concern
    return {"deny": None, "writes": writes}


# ---- MCP surface (shared) -------------------------------------------------
MCP_ORG_KEYS = ("targetOrg", "target-org", "targetusername", "username", "usernameOrAlias", "org")
MCP_READ = re.compile(r"(query|describe|list|get|retrieve|report|preview|display|overview|"
                      r"logs?|read|search|installed|status|resume|analyze|rules?|username)$", re.I)
MCP_WRITEISH = re.compile(r"(deploy|delete|create|update|upsert|assign|execute|apex|anonymous|"
                          r"bulk|purge|destroy|install|uninstall|import|merge|undelete)", re.I)


def mcp_target(tinput):
    return next((tinput.get(k) for k in MCP_ORG_KEYS if tinput.get(k)), None)


def mcp_analyze(tool, tinput):
    """Returns {'read':True} | {'write':target, 'destructive':(op,digest,body)|None}.
    DEFAULT-DENY: an org-touching tool not on the read allowlist is a write (audit T10-04)."""
    name = tool.split("__")[-1].lower()
    target = mcp_target(tinput)
    if MCP_READ.search(name):
        return {"read": True}
    if target is None and not MCP_WRITEISH.search(name):
        return {"read": True}                          # local, non-org tool (e.g. code analyzer)
    dest = None
    if "apex" in name or "anonymous" in name:
        body = tinput.get("apexCode") or tinput.get("apex") or tinput.get("code") or ""
        dest = ("apex", hashlib.sha256(body.encode()).hexdigest()[:16], body)
    elif ("delete" in name or "purge" in name or "destroy" in name) and ("bulk" in name or "hard" in name):
        dest = ("bulk-delete", "", str(tinput))
    elif "bulk" in name or "import" in name:
        dest = ("bulk-write", "", str(tinput))
    elif name.endswith("delete_record") and not (tinput.get("recordId") or tinput.get("id")
                                                 or tinput.get("record-id")):
        dest = ("where-delete", "", str(tinput))
    elif (name.endswith("update_record") or name.endswith("upsert_record")) and not (
            tinput.get("recordId") or tinput.get("id") or tinput.get("record-id")):
        dest = ("where-update", "", str(tinput))
    elif "deploy" in name and (tinput.get("preDestructiveChanges") or tinput.get("postDestructiveChanges")
                               or tinput.get("pre-destructive-changes") or tinput.get("post-destructive-changes")):
        dest = ("destructive-metadata", "", str(tinput))
    return {"write": target, "destructive": dest}
