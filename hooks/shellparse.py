"""Shared shell + Salesforce-CLI analysis for the Torque gates.

ONE classifier, used by BOTH prod_write_gate and destructive_data_gate, so the two hooks can
never disagree about whether a command runs `sf` or which subcommand it is. The round-10
panel (codex + kimi executed-vs-real-sf + Claude hostile-qa) proved the gates must decide
"is this sf, and which subcommand" from a single, shared, expansion-aware argv classifier —
never from raw-text regex, and never by resolving through wrappers.

Design law: FAIL CLOSED on any segment that could reach `sf` but whose command cannot be
statically resolved to the literal program `sf`/`sfdx`. This process performs NO shell
expansion, so anything that only becomes `sf` at exec time is denied, not guessed:
  - parameter/ANSI-C indirection  x=sf; $x …   /   s$'\\x66' …            (indirect argv0)
  - command/process substitution  $(…) `…` <(…) >(…)  and ( ) { } groups
  - wrappers & unknown runners     nice -n 5 sf …  sudo -u root sf …  caffeinate sf …
  - interpreters & here-strings    bash -c '…sf…'   eval sf …   bash <<< 'sf …'
  - xargs/parallel stdin commands  echo sf … | xargs -J{} {}
  - cd-desync writes to the gate   cd hooks && echo x > lib.py
"""
import os, re, shlex, hashlib
from pathlib import Path
import lib

TARGET_FLAGS = {"--target-org", "-o", "--targetusername", "-u"}
INTERPRETERS = {"bash", "sh", "zsh", "dash", "ksh", "fish", "eval", "source", ".",
                "python", "python2", "python3", "perl", "ruby", "node", "deno", "awk",
                "xargs", "parallel"}
# Programs that exec another command given as their argument(s). We never resolve THROUGH them
# (arity is unsound — audit R10-04); if one is argv0 and any literal `sf` token appears, deny.
WRAPPERS = {"env", "nice", "sudo", "doas", "ionice", "stdbuf", "nohup", "setsid", "time",
            "command", "exec", "builtin", "caffeinate", "arch", "timeout", "flock", "script",
            "watch", "chrt", "taskset", "unbuffer", "gtimeout", "proxychains", "strace", "ltrace"}

# sf/sfdx subcommands that only READ — no org authorization required. Broad prefixes that hide
# writes (package version create/promote, plugins install) were removed (audit T10-05/R10-R1).
SF_READS = {
    ("data", "query"), ("data", "search"), ("data", "export"), ("data", "resume"),
    ("sobject", "describe"), ("sobject", "list"),
    ("limits",), ("doctor",), ("version",), ("help",), ("which",),
    ("org", "display"), ("org", "list"), ("org", "open"), ("org", "login"), ("org", "logout"),
    ("apex", "list"), ("apex", "tail"), ("apex", "get"),
    ("project", "retrieve"), ("project", "generate"), ("project", "convert"),
    ("package", "installed"), ("config", "get"), ("config", "list"),
    ("alias",), ("autocomplete",), ("info",), ("community", "list"), ("agent", "preview"),
}
SF_READ3 = {("package", "version", "list"), ("package", "version", "report"),
            ("package", "version", "displayancestry"), ("package", "version", "displaydependencies"),
            ("schema", "generate", "field"), ("schema", "generate", "sobject"),
            ("schema", "generate", "platformevent"), ("schema", "generate", "tab")}
COLON_READ = ("force:schema", "force:org:display", "force:org:list", "force:org:open",
              "force:auth", "force:data:soql:query", "force:data:record:get",
              "force:data:tree:export", "force:mdapi:retrieve", "force:source:retrieve",
              "force:package:installed", "force:apex:log", "force:apex:class:list")

# "sf-suspicious": a sign of a Salesforce WRITE. Keyed on the long org flags and write-shaped
# subcommand verbs — NOT the short -o/-u (which false-positive on `ls -o`, `sort -o`) and NOT
# the literal token "sf" (which false-positives on `grep sf`). Over-broad here only ever fires
# a deny on a NON-literal-sf command, which is safe.
SF_SUSPICIOUS = re.compile(
    r"(--target-org|--targetusername|--flags-dir|"
    r"\b(apex\s+run|data\s+(create|update|delete|upsert|import)|"
    r"project\s+deploy|project\s+delete\s+source|org\s+(assign|create|delete|enable|disable)|"
    r"user\s+(create|password)|package\s+(version|install|uninstall|create|delete|promote)|"
    r"force:(data|apex|source|mdapi|package)|force:org:(create|delete)))", re.I)
SF_WORD = re.compile(r"(?:^|[\s'\";|&(`])s\s*f(?:dx)?(?:[\s'\";|&)`]|$)", re.I)

WRITE_SHAPE_CMDS = {"cp", "mv", "dd", "tee", "ln", "install", "truncate", "rsync"}
PERM_CMDS = {"chmod", "chown", "chgrp", "chflags", "rm", "rmdir", "unlink", "shred"}
REDIR_RE = re.compile(r"^&?\d*>>?\|?$")
# Distinctive gate/config filenames that must never be a write target, at ANY path (audit
# R10-06 defense-in-depth — resolves the cd-desync class even if cwd tracking is imperfect).
PROTECTED_BASENAMES = {"lib.py", "shellparse.py", "prod_write_gate.py", "destructive_data_gate.py",
                       "lib_cli.py", "settings.json", "writable-orgs.json", "protected-objects",
                       "cli-write-surface.json", "clean-ip.rules", ".classify-cache.json",
                       "audit.log", "torque-approve", "torque-frontdoor", "torque-install-gates",
                       "validate.py"}


def strip_continuations(cmd: str) -> str:
    return re.sub(r"\\\n", "", cmd)                   # shell removes backslash-newline


def split_segments(cmd: str):
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
        if c in (";", "|", "&", "\n", "{", "}", "(", ")"):     # groups are boundaries (R10-02)
            segs.append("".join(buf)); buf = []; i += 1; continue
        buf.append(c); i += 1
    if quote:
        return segs, False
    segs.append("".join(buf))
    return segs, True


def grouping_or_subst(cmd: str) -> bool:
    return ("$(" in cmd or "`" in cmd or "<(" in cmd or ">(" in cmd
            or bool(re.search(r"(?:^|[\s;&|])[({]", cmd)))


def anchor_ref(tok: str) -> bool:
    low = tok.lower()
    if ".torque" not in low:
        return False
    return "/approved/" not in low and not low.rstrip("/").endswith(".torque/approved")


def indirect(tok: str) -> bool:
    return any(c in tok for c in "$`{}")


def _strip_assignments(argv):
    i, vals = 0, []
    while i < len(argv) and re.match(r"^[A-Za-z_]\w*=", argv[i]):
        vals.append(argv[i].split("=", 1)[1]); i += 1
    return argv[i:], vals


def _cut_ddash(args):
    return args[:args.index("--")] if "--" in args else args


def targets(sf_args):
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


def is_dry_run(sf_args):
    return any(a in ("--dry-run", "--checkonly", "--check-only") for a in sf_args)


def subcommand(sf_args):
    """The leading positional subcommand path (`sf <topic> <action> …`), stopping at the first
    flag — flag VALUES are not part of the subcommand and must not pollute it."""
    out = []
    for a in _cut_ddash(sf_args):
        if a.startswith("-"):
            break
        out.append(a.lower())
    return tuple(out)


def has_record_id(sf_args):
    for a in _cut_ddash(sf_args):
        if a in ("-i", "--record-id") or a.startswith("--record-id=") or re.match(r"^-i\S", a):
            return True
    return False


def file_value(sf_args):
    args = _cut_ddash(sf_args)
    for i, a in enumerate(args):
        if a in ("--file", "-f") and i + 1 < len(args):
            return args[i + 1]
        if a.startswith("--file="):
            return a[len("--file="):]
    return None


def classify_destructive(sf_args):
    """Return op_class if the parsed sf write is destructive, else None. Parsed argv only —
    never raw-text regex (audit T10-02/R10-R2)."""
    sub = subcommand(sf_args)
    if not sub:
        return None
    if sub[:2] == ("apex", "run") or sub[0].startswith("force:apex"):
        return "apex"
    if sub[:3] == ("data", "delete", "bulk") or "--hard-delete" in sf_args:
        return "bulk-delete"
    if sub[:3] == ("data", "update", "bulk") or sub[:2] == ("data", "import") \
       or sub[:2] == ("data", "upsert"):
        return "bulk-write"
    if sub[:3] == ("data", "delete", "record") and not has_record_id(sf_args):
        return "where-delete"
    if sub[:3] == ("data", "update", "record") and not has_record_id(sf_args):
        return "where-update"
    if sub[:3] == ("project", "delete", "source") or sub[0].startswith("force:source:delete") \
       or any(a in ("--pre-destructive-changes", "--post-destructive-changes")
              or a.startswith(("--pre-destructive-changes=", "--post-destructive-changes="))
              for a in sf_args):
        return "destructive-metadata"
    return None


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
    # a validate-only / dry-run deploy pulls nothing and writes nothing → treat as a read
    if pos[:2] == ("project", "deploy") and is_dry_run(sf_args):
        return True
    key1 = (first,)
    key2 = (first, pos[1]) if len(pos) > 1 else None
    return key1 in SF_READS or (key2 is not None and key2 in SF_READS)


def _write_shape_targets(argv):
    base0 = os.path.basename(argv[0]).lower() if argv else ""
    cand = []
    for i, tok in enumerate(argv):
        if REDIR_RE.match(tok) and i + 1 < len(argv):
            cand.append(argv[i + 1])
        elif tok.startswith((">", ">>")) and len(tok.lstrip(">|&")) > 0:
            cand.append(tok.lstrip(">|&"))
    if base0 in WRITE_SHAPE_CMDS or (base0 == "sed" and "-i" in argv) or base0 in PERM_CMDS:
        cand += [a for a in argv[1:] if not a.startswith("-")]
        cand += [a.split("=", 1)[1] for a in argv if a.startswith("of=")]
    return base0, cand


def check_write_shapes(segs):
    """Walk segments tracking cwd (cd-desync, R10-06); deny a write/perm op whose target is the
    trust anchor, a protected path, the local store, or a distinctive gate filename. Returns a
    (reason, fingerprint) deny tuple or None."""
    cwd = Path(os.environ.get("TORQUE_HOME", os.getcwd()))
    try:
        cwd = Path(os.getcwd())
    except Exception:
        pass
    for seg in segs:
        seg = seg.strip()
        if not seg:
            continue
        try:
            argv = shlex.split(seg)
        except ValueError:
            continue
        if not argv:
            continue
        base0 = os.path.basename(argv[0]).lower()
        if base0 in ("cd", "pushd") and len(argv) > 1:
            tgt = os.path.expanduser(argv[1])
            newcwd = Path(tgt) if os.path.isabs(tgt) else (cwd / tgt)
            # cd INTO a protected dir is itself the R10-06 setup → deny
            try:
                r = newcwd.resolve()
                if lib.is_protected_target(str(r)) or anchor_ref(str(r)) or ".torque" in str(r).lower():
                    return ("cd into a protected directory (gate/anchor) — refused", "cd-protected")
            except Exception:
                pass
            cwd = newcwd
            continue
        _, cands = _write_shape_targets(argv)
        for c in cands:
            if not c:
                continue
            if os.path.basename(c) in PROTECTED_BASENAMES:
                return (f"write to a protected gate file: {os.path.basename(c)}", "protected-write")
            cc = os.path.expanduser(c)
            rp = cc if os.path.isabs(cc) else str((cwd / cc))
            try:
                rp = str(Path(rp).resolve())
            except Exception:
                pass
            if (anchor_ref(rp) or lib.is_protected_target(rp) or _under_local(rp)):
                return (f"write to a protected path: {c}", "protected-write")
    return None


def _under_local(path_str):
    try:
        rp = str(Path(os.path.expanduser(path_str)).resolve())
    except Exception:
        return False
    lp = str(lib.LOCAL.resolve())
    return rp == lp or rp.startswith(lp + "/")


def analyze_bash(cmd: str):
    """{'deny': (reason, fingerprint)} to block, or {'deny': None, 'writes': [sf_args, …]}.
    `writes` = args AFTER the sf/sfdx token for each DIRECT sf WRITE. Fails closed throughout."""
    cmd = strip_continuations(cmd)
    segs, ok = split_segments(cmd)
    if not ok:
        return {"deny": ("unparseable command (unbalanced quotes)", "unparseable")}
    if grouping_or_subst(cmd) and (SF_SUSPICIOUS.search(cmd) or SF_WORD.search(cmd)):
        return {"deny": ("command grouping/substitution around a Salesforce operation — "
                         "not statically authorizable", "substitution")}
    ws = check_write_shapes(segs)
    if ws:
        return {"deny": ws}
    # xargs/parallel take the command word from stdin — undecidable; deny if sf appears anywhere
    if re.search(r"(?:^|[\s;&|])(xargs|parallel)\b", cmd) and (SF_WORD.search(cmd) or SF_SUSPICIOUS.search(cmd)):
        return {"deny": ("sf routed through xargs/parallel — target not authorizable", "indirect-sf")}
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
                return {"deny": ("reference to the trust anchor (~/.torque) — secret and tokens "
                                 "are operator-only", "anchor-ref")}
        a, assign_vals = _strip_assignments(argv)
        # an assignment whose VALUE carries a Salesforce op is a hide-then-expand (R10-03)
        for v in assign_vals:
            if SF_SUSPICIOUS.search(v):
                return {"deny": ("Salesforce operation hidden in a shell assignment value",
                                 "indirect-sf")}
        if not a:
            continue
        base0 = os.path.basename(a[0]).lower()
        suspicious = bool(SF_SUSPICIOUS.search(seg))
        if base0 in ("sf", "sfdx"):
            sf_args = a[1:]
            if has_flags_dir(sf_args) and not is_read(sf_args):
                return {"deny": ("sf --flags-dir can inject a target from a file — unsupported "
                                 "on writes", "flags-dir")}
            if is_read(sf_args):
                continue
            writes.append(sf_args)
            continue
        if indirect(a[0]):
            return {"deny": ("indirect command invocation ($VAR/$()/backtick) cannot be "
                             "authorized — call `sf` literally", "indirect-argv0")}
        if base0 in INTERPRETERS and (suspicious or SF_WORD.search(seg)):
            return {"deny": (f"Salesforce operation via interpreter/here-string ({base0}) — "
                             "not authorizable", "interp-sf")}
        if base0 in WRAPPERS and (suspicious or SF_WORD.search(seg)):
            return {"deny": (f"Salesforce operation under a wrapper/runner ({base0}) — call "
                             "`sf` directly", "wrapper-sf")}
        if suspicious:
            return {"deny": ("Salesforce operation under an unrecognized command — not "
                             "authorizable", "wrapper-sf")}
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
    """{'read':True} | {'write':target, 'destructive':(op,digest,body)|None}. DEFAULT-DENY: an
    org-touching tool not on the read allowlist is a write (audit T10-04/R10-08)."""
    name = tool.split("__")[-1].lower()
    target = mcp_target(tinput)
    if MCP_READ.search(name):
        return {"read": True}
    if target is None and not MCP_WRITEISH.search(name):
        return {"read": True}
    dest = None
    if re.search(r"(execute_)?(anonymous_)?apex|anonymous", name):
        body = tinput.get("apexCode") or tinput.get("apex") or tinput.get("code") or ""
        dest = ("apex", hashlib.sha256(body.encode()).hexdigest()[:16], body)
    elif re.search(r"(delete|purge|destroy)", name) and re.search(r"(bulk|hard)", name):
        dest = ("bulk-delete", "", str(tinput))
    elif re.search(r"bulk|import", name):
        dest = ("bulk-write", "", str(tinput))
    elif re.search(r"delete_records?$", name) and not (tinput.get("recordId") or tinput.get("id")
                                                       or tinput.get("record-id")):
        dest = ("where-delete", "", str(tinput))
    elif re.search(r"(update|upsert)_records?$", name) and not (tinput.get("recordId")
             or tinput.get("id") or tinput.get("record-id")):
        dest = ("where-update", "", str(tinput))
    elif "deploy" in name and (tinput.get("preDestructiveChanges") or tinput.get("postDestructiveChanges")
                               or tinput.get("pre-destructive-changes") or tinput.get("post-destructive-changes")):
        dest = ("destructive-metadata", "", str(tinput))
    return {"write": target, "destructive": dest}
