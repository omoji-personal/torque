"""Shared shell + Salesforce-CLI analysis for the Torque gates.

ONE classifier, used by BOTH prod_write_gate and destructive_data_gate, so the two hooks can
never disagree about whether a command runs `sf`. Rounds 10–11 (codex shell-semantics, kimi
executed-vs-real-sf, Claude hostile-qa) proved the gates must decide "is this sf, and which
subcommand" from parsed argv — never raw-text regex — and must FAIL CLOSED on any indirection
that could reach `sf` but cannot be statically resolved. Every bypass found routed AROUND the
sf-base0 classifier (relocation under a runner, glued redirect, legacy colon syntax, MCP
naming), so the fixes harden those seams while keeping direct-`sf` authorization intact.
"""
import os, re, shlex, hashlib, fnmatch
from pathlib import Path
import lib

TARGET_FLAGS = {"--target-org", "-o", "--targetusername", "-u"}
INTERPRETERS = {"bash", "sh", "zsh", "dash", "ksh", "fish", "eval", "source", ".",
                "python", "python2", "python3", "perl", "ruby", "node", "deno", "awk"}
# sf CLI topics — a standalone `sf` token followed by one of these (or a target flag) is an sf
# invocation, even under an unknown runner. Lets `grep sf file` through (no topic follows).
SF_TOPICS = {"data", "sobject", "org", "project", "apex", "package", "schema", "alias",
             "config", "limits", "user", "community", "agent", "api", "auth", "doctor",
             "plugins", "which", "autocomplete", "info", "version", "help", "deploy",
             "retrieve", "source", "mdapi", "lightning", "static-resource"}
# sf global flags that may precede the verb; skipped when locating the subcommand.
GLOBAL_FLAGS = {"--json", "--loglevel", "--flags-dir"}
# opaque file writers that can rewrite the gate without naming a simple target (extract/patch).
OPAQUE_WRITERS = {"patch", "unzip", "cpio", "ditto", "unar", "7z", "7za", "gpatch"}
GIT_WRITE_SUBS = {"checkout", "restore", "reset", "switch", "rm", "mv", "clean", "stash",
                  "apply", "am", "revert", "cherry-pick"}

SF_READS = {
    ("data", "query"), ("data", "search"), ("data", "export"), ("data", "resume"),
    ("sobject", "describe"),
    ("sobject", "list"), ("limits",), ("doctor",), ("version",), ("help",), ("which",),
    ("org", "display"), ("org", "list"), ("org", "open"), ("org", "login"), ("org", "logout"),
    ("apex", "list"), ("apex", "tail"), ("apex", "get"), ("project", "retrieve"),
    ("project", "generate"), ("project", "convert"), ("package", "installed"),
    ("config", "get"), ("config", "list"), ("alias",), ("autocomplete",), ("info",),
    ("community", "list"), ("agent", "preview"),
}
SF_READ3 = {("package", "version", "list"), ("package", "version", "report"),
            ("package", "version", "displayancestry"), ("package", "version", "displaydependencies"),
            ("schema", "generate", "field"), ("schema", "generate", "sobject"),
            ("schema", "generate", "platformevent"), ("schema", "generate", "tab")}
COLON_READ = ("force:schema", "force:org:display", "force:org:list", "force:org:open",
              "force:auth", "force:data:soql:query", "force:data:record:get",
              "force:data:tree:export", "force:mdapi:retrieve", "force:source:retrieve",
              "force:package:installed", "force:apex:log", "force:apex:class:list")

SF_SUSPICIOUS = re.compile(
    r"(--target-org|--targetusername|--flags-dir|"
    r"\b(apex\s+run|data\s+(create|update|delete|upsert|import)|"
    r"project\s+deploy|project\s+delete\s+source|org\s+(assign|create|delete|enable|disable)|"
    r"user\s+(create|password)|package\s+(version|install|uninstall|create|delete|promote)|"
    r"api\s+request|force:(data|apex|source|mdapi|package)|force:org:(create|delete)))", re.I)
SF_WORD = re.compile(r"(?:^|[\s'\";|&(`])s\s*f(?:dx)?(?:[\s'\";|&)`]|$)", re.I)

WRITE_SHAPE_CMDS = {"cp", "mv", "dd", "tee", "ln", "install", "truncate", "rsync"}
PERM_CMDS = {"chmod", "chown", "chgrp", "chflags", "rm", "rmdir", "unlink", "shred"}
CD_FLAGS = {"-P", "-L", "-e", "-@", "-"}
REDIR_FUSED = re.compile(r"^(?:\{\w+\}|&|\d+)?<?>{1,2}[|!&]?(.*)$")  # > >> 1> 2<> &> >| >& {fd}> >! (+ glued path)
PROTECTED_BASENAMES = {"lib.py", "shellparse.py", "prod_write_gate.py", "destructive_data_gate.py",
                       "lib_cli.py", "settings.json", "writable-orgs.json", "protected-objects",
                       "cli-write-surface.json", "clean-ip.rules", ".classify-cache.json",
                       "audit.log", "torque-approve", "torque-frontdoor", "torque-install-gates",
                       "validate.py"}


def strip_continuations(cmd: str) -> str:
    return re.sub(r"\\\n", "", cmd)


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
        if c in ("|", "&") and buf and buf[-1] == ">":  # `>|` noclobber / `>&` dup are redirects
            buf.append(c); i += 1; continue
        if c == "&" and i + 1 < n and cmd[i + 1] == ">":  # `&>` redirect, not a background op
            buf.append(c); i += 1; continue
        if c in ("{", "}"):
            # a brace is a group boundary only as a STANDALONE token (space/start/`;` around it);
            # inside a word (`Name=foo{bar}`, named-fd `{fd}>`) it stays part of the token (TQ-009)
            prev = buf[-1] if buf else " "
            nxt = cmd[i + 1] if i + 1 < n else " "
            if prev in (" ", "\t", "\n", ";", "") and nxt in (" ", "\t", "\n", ";", ""):
                segs.append("".join(buf)); buf = []; i += 1; continue
            buf.append(c); i += 1; continue
        if c in (";", "|", "&", "\n", "(", ")"):
            segs.append("".join(buf)); buf = []; i += 1; continue
        buf.append(c); i += 1
    if quote:
        return segs, False
    segs.append("".join(buf))
    return segs, True


def grouping_or_subst(cmd: str) -> bool:
    return ("$(" in cmd or "`" in cmd or "<(" in cmd or ">(" in cmd
            or bool(re.search(r"(?:^|[\s;&|])[({]", cmd)))


def _abs_pattern(tok, cwd=None):
    """A token as an ABSOLUTE glob PATTERN, expansion-aware: ~ expanded, $var/${}/backtick → '*'
    (any shell var could construct any path), relative made absolute against cwd. The gate sees
    PRE-EXPANSION text; bash expands globs/vars AFTER the hook (audit T12-01/02), so trust
    decisions must be made on what the token COULD become, not its literal form."""
    t = os.path.expanduser(tok)
    t = re.sub(r"\$\{[^}]*\}|\$\w+|`[^`]*`", "*", t)
    if not os.path.isabs(t):
        t = os.path.join(str(cwd if cwd is not None else _safe_cwd()), t)
    # realpath (not normpath) so a symlinked prefix (macOS /tmp→/private/tmp) matches the
    # anchor's own .resolve(); wildcard components that don't exist are left intact (audit R11-10)
    return os.path.realpath(t)


def _safe_cwd():
    try:
        return os.getcwd()
    except Exception:
        return os.environ.get("TORQUE_HOME", "/")


def _pattern_reaches_dir(pat, dirpath):
    """True if glob PATTERN `pat` could match a path AT or UNDER `dirpath`, comparing components
    with fnmatch (so `~/.torq*` matches the `.torque` component and `$a$b`→`*` matches it too)."""
    pc, dc = pat.split(os.sep), dirpath.split(os.sep)
    if len(pc) < len(dc):
        return False
    return all(fnmatch.fnmatch(d, pc[i]) for i, d in enumerate(dc))


def anchor_ref(tok, cwd=None) -> bool:
    """Deny any reference to the trust anchor EXCEPT a read of the approved-apex copy — resolved
    against the ACTUAL anchor paths AND expansion-aware, so `cat ~/.torq*/secret`,
    `a=.tor;b=que;cat ~/$a$b/secret`, and a custom TORQUE_ANCHOR all deny (audit T12-01)."""
    pat = _abs_pattern(tok, cwd)
    approved = str(lib.APPROVED.resolve())
    if pat == approved or pat.startswith(approved + os.sep):
        return False                                  # the approved-apex copy is readable
    if ".torque" in tok.lower() and "/approved/" not in tok.lower():
        return True
    if fnmatch.fnmatch(str(lib.SECRET.resolve()), pat):
        return True
    if _pattern_reaches_dir(pat, str(lib.ANCHOR.resolve())):
        return True
    base = os.path.basename(pat)
    if any(c in pat for c in "*?[") and (base == "secret" or fnmatch.fnmatch(base, "*.token")
                                         or fnmatch.fnmatch(base, "*.grant")):
        return True
    return False


def sf_auth_ref(tok) -> bool:
    """The sf CLI auth store (~/.sfdx, ~/.sf) holds live access tokens — an agent reading it via
    Bash (`cat ~/.sfd*/x.json`) could lift a token and curl the REST API, bypassing sf entirely.
    Expansion-aware, same as anchor_ref (audit T12-01 applied to the auth store)."""
    pat = _abs_pattern(tok)
    for d in ("~/.sfdx", "~/.sf"):
        if _pattern_reaches_dir(pat, os.path.realpath(os.path.expanduser(d))):
            return True
    return False


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


def sobject_value(sf_args):
    args = _cut_ddash(sf_args)
    for i, a in enumerate(args):
        if a in ("--sobject", "-s", "--sobjecttype", "--sobjecttypecategory") and i + 1 < len(args):
            return args[i + 1]
        for f in ("--sobject=", "-s=", "--sobjecttype="):
            if a.startswith(f):
                return a[len(f):]
    return None


def subcommand(sf_args):
    """Leading positional subcommand path, skipping global flags (and their values) that may
    precede the verb (audit R11-10). Stops at the first non-global flag."""
    args = _cut_ddash(sf_args)
    out, i = [], 0
    while i < len(args):
        a = args[i]
        if a.startswith("-"):
            base = a.split("=", 1)[0]
            if base in GLOBAL_FLAGS:
                if "=" not in a and base in ("--loglevel", "--flags-dir") and i + 1 < len(args):
                    i += 2; continue
                i += 1; continue
            break
        out.append(a.lower()); i += 1
    return tuple(out)


def is_read(sf_args) -> bool:
    pos = subcommand(sf_args)
    if not pos:
        # empty subcommand but a positional exists (a leading `--` or unknown flag hid the verb)
        # ⇒ fail CLOSED, treat as NOT read (audit T12-04). Scan the FULL args, not _cut_ddash.
        return not any(not a.startswith("-") for a in sf_args)
    first = pos[0]
    if ":" in first:
        return first.startswith(COLON_READ)
    if first == "package" and len(pos) >= 2 and pos[1] == "version":
        return pos[:3] in SF_READ3
    if first == "schema" and len(pos) >= 3:
        return pos[:3] in SF_READ3
    if pos[:2] == ("project", "deploy") and is_dry_run(sf_args):
        return True
    key1 = (first,)
    key2 = (first, pos[1]) if len(pos) > 1 else None
    return key1 in SF_READS or (key2 is not None and key2 in SF_READS)


def classify_destructive(sf_args):
    """Op-class if the parsed sf write is destructive, else None. Covers modern space syntax AND
    legacy colon syntax (audit R11-04/R11-05) and async-resume completion of bulk jobs."""
    sub = subcommand(sf_args)
    if not sub:
        # a leading `--`/unknown flag hid the verb — re-scan ALL positionals, fail CLOSED (T12-04)
        sub = tuple(a.lower() for a in sf_args if not a.startswith("-"))
        if not sub:
            return None
    f = sub[0]
    if sub[:2] == ("apex", "run") or f.startswith("force:apex:execute"):
        return "apex"                                 # NOT force:apex:test:run (that's a test, TQ-011)
    if sub[:2] == ("org", "delete") or f.startswith("force:org:delete"):
        return "org-delete"                           # destroy a sandbox/scratch org (RU-2)
    if sub[:3] == ("data", "delete", "bulk") or "--hard-delete" in sf_args \
       or f.startswith("force:data:bulk:delete"):
        return "bulk-delete"
    if sub[:3] == ("data", "update", "bulk") or sub[:2] == ("data", "import") \
       or sub[:2] == ("data", "upsert") or f.startswith(("force:data:bulk:upsert",
       "force:data:bulk:update", "force:data:tree:import")):
        return "bulk-write"
    if (sub[:3] == ("data", "delete", "record") or f.startswith("force:data:record:delete")) \
       and not has_record_id(sf_args):
        return "where-delete"
    if (sub[:3] == ("data", "update", "record") or f.startswith("force:data:record:update")) \
       and not has_record_id(sf_args):
        return "where-update"
    if sub[:3] in (("data", "delete", "resume"), ("data", "update", "resume"),
                   ("data", "upsert", "resume")):
        return "bulk-write"
    if sub[:3] == ("project", "delete", "source") or f.startswith("force:source:delete") \
       or any(a in ("--pre-destructive-changes", "--post-destructive-changes")
              or a.startswith(("--pre-destructive-changes=", "--post-destructive-changes="))
              for a in sf_args) or "destructivechanges" in " ".join(sf_args).lower():
        return "destructive-metadata"
    return None


def wrapped_sf(argv):
    """True if some token is a standalone sf/sfdx AND the tokens after it look like an sf
    invocation (an sf topic, colon-form, or a target flag). Distinguishes `nice sf data delete`
    (deny) from `grep sf file` / `echo 'sf ...'` (sf is a search/quoted arg — allow)."""
    for i, t in enumerate(argv):
        if os.path.basename(t).lower() in ("sf", "sfdx"):
            rest = argv[i + 1:]
            if not rest:
                continue
            if targets(rest):                       # any explicit target flag
                return True
            sub = subcommand(rest)                  # skips leading global flags + values (TQ-004)
            if sub and (sub[0].startswith("force:") or sub[0].split(":")[0] in SF_TOPICS
                        or sub[0] in SF_TOPICS):
                return True
    return False


def _sed_inplace(argv):
    return any(a == "-i" or a.startswith("-i") or a.startswith("--in-place") for a in argv[1:])


def _write_shape_targets(argv):
    base0 = os.path.basename(argv[0]).lower() if argv else ""
    cand = []
    for i, tok in enumerate(argv):
        m = REDIR_FUSED.match(tok)
        if m:
            if m.group(1):
                cand.append(m.group(1))               # fused: 2>path, >|path, >&path
            elif i + 1 < len(argv):
                cand.append(argv[i + 1])              # bare: > path
    if base0 in WRITE_SHAPE_CMDS or (base0 == "sed" and _sed_inplace(argv)) or base0 in PERM_CMDS:
        cand += [a for a in argv[1:] if not a.startswith("-")]
        cand += [a.split("=", 1)[1] for a in argv if a.startswith("of=")]
    return base0, cand


def _protected_path(pathlike, cwd=None):
    if not pathlike:
        return False
    pat = _abs_pattern(pathlike, cwd)
    base = os.path.basename(pat.rstrip("/")) or pat
    # literal OR globbed basename of a distinctive gate file (`settings.jso*` → settings.json)
    for b in PROTECTED_BASENAMES:
        if fnmatch.fnmatch(b, base) or fnmatch.fnmatch(base, b):
            return True
    if anchor_ref(pathlike, cwd):
        return True
    dirs = lib.protected_write_paths() + [str((lib.TORQUE_HOME / ".claude").resolve()),
                                          str(lib.LOCAL.resolve())]
    return any(_pattern_reaches_dir(pat, d) or pat == d or pat.startswith(d + os.sep) for d in dirs)


def _under_local(path_str):
    try:
        rp = str(Path(os.path.expanduser(path_str)).resolve())
    except Exception:
        return False
    lp = str(lib.LOCAL.resolve())
    return rp == lp or rp.startswith(lp + os.sep)


def check_write_shapes(segs):
    """Walk segments tracking cwd (cd-desync); deny a write/perm/opaque-writer op that reaches a
    protected path or the gate itself. Returns (reason, fingerprint) or None. Fails closed on
    opaque extractors/patchers and VCS restores that could rewrite the gate (audit R11-03)."""
    try:
        initial = Path(os.getcwd())
    except Exception:
        initial = Path(os.environ.get("TORQUE_HOME", "."))
    cwd = initial
    home = Path(os.path.expanduser("~"))
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
        if base0 in ("cd", "pushd"):
            rest = [a for a in argv[1:] if a not in CD_FLAGS and not a.startswith("-")]
            if rest:                                    # cd <dir>
                tgt = os.path.expanduser(rest[0])
                newcwd = Path(tgt) if os.path.isabs(tgt) else (cwd / tgt)
                try:
                    r = newcwd.resolve()
                    if lib.is_protected_target(str(r)) or anchor_ref(str(r)) or ".torque" in str(r).lower():
                        return ("cd into a protected directory (gate/anchor) — refused", "cd-protected")
                except Exception:
                    pass
                cwd = newcwd
            else:
                cwd = home                              # bare `cd` → HOME (audit TQ-006)
            continue
        if base0 in OPAQUE_WRITERS:
            return (f"opaque file writer ({base0}) can rewrite gate files — refused", "opaque-writer")
        if base0 in ("tar", "bsdtar", "gtar", "pax"):
            opts = [a for a in argv[1:] if a.startswith("-")]
            mode1 = argv[1] if len(argv) > 1 and not argv[1].startswith("-") else ""
            extracting = ("--extract" in argv
                          or any("x" in o.lstrip("-") for o in opts)
                          or (mode1 and "x" in mode1 and all(ch in "xtczjJvfahmpPkWO" for ch in mode1)))
            if extracting:                              # only genuine extract, not `tar -tf x.tar` (TQ-012)
                return (f"archive extraction ({base0}) can rewrite gate files — refused", "opaque-writer")
        if base0 == "git":
            gi = 1                                      # skip git global options + values (TQ-007)
            while gi < len(argv):
                a = argv[gi]
                if a in ("-C", "-c", "--git-dir", "--work-tree", "--namespace"):
                    gi += 2; continue
                if a.startswith("-"):
                    gi += 1; continue
                break
            sub = argv[gi] if gi < len(argv) else ""
            if sub in GIT_WRITE_SUBS:
                if sub in ("apply", "am", "stash", "cherry-pick", "revert"):
                    return (f"git {sub} can rewrite tracked gate files — refused", "opaque-writer")
                after = argv[gi + 1:]
                paths = after[after.index("--") + 1:] if "--" in after else [a for a in after if not a.startswith("-")]
                for p in paths:
                    if _protected_path(p, cwd) or _protected_path(p, initial) \
                       or os.path.basename(p.rstrip("/")) in ("hooks", "bin", ".claude", "checks"):
                        return (f"git {sub} targeting protected paths — refused", "protected-write")
            continue
        _, cands = _write_shape_targets(argv)
        for c in cands:
            # union of tracked cwd AND the real starting cwd — a subshell `(cd x)` cannot
            # persist, so a relative write still resolves against the original dir (audit T12-06)
            if _protected_path(c, cwd) or _protected_path(c, initial):
                return (f"write to a protected path: {c}", "protected-write")
    return None


def _is_org_mutation(sf_args):
    """sf subcommands that change the alias/config/auth mapping a later target resolves through."""
    sub = subcommand(sf_args)
    return sub[:2] in (("alias", "set"), ("alias", "unset"), ("config", "set"),
                       ("config", "unset"), ("org", "login"), ("org", "logout"))


def analyze_bash(cmd: str):
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
    if re.search(r"(?:^|[\s;&|])(xargs|parallel)\b", cmd) and (SF_WORD.search(cmd) or SF_SUSPICIOUS.search(cmd)):
        return {"deny": ("sf routed through xargs/parallel — target not authorizable", "indirect-sf")}
    writes, mutations = [], []
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
            if sf_auth_ref(tok):
                return {"deny": ("reference to the sf auth store (~/.sfdx, ~/.sf) — it holds live "
                                 "access tokens", "auth-ref")}
        a, assign_vals = _strip_assignments(argv)
        for v in assign_vals:
            if SF_SUSPICIOUS.search(v) or os.path.basename(v).lower() in ("sf", "sfdx"):
                return {"deny": ("Salesforce operation hidden in a shell assignment value",
                                 "indirect-sf")}
        if not a:
            continue
        base0 = os.path.basename(a[0]).lower()
        if base0 in ("sf", "sfdx"):
            sf_args = a[1:]
            if _is_org_mutation(sf_args):
                mutations.append(sf_args)             # alias/config/login re-points a target (TQ-002)
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
        if base0 in INTERPRETERS and (SF_WORD.search(seg) or SF_SUSPICIOUS.search(seg)
                                      or "$" in seg or "`" in seg):
            return {"deny": (f"Salesforce operation via interpreter/here-string ({base0}) — "
                             "not authorizable", "interp-sf")}
        if wrapped_sf(a):
            return {"deny": ("Salesforce operation under a wrapper/runner — call `sf` directly",
                             "wrapper-sf")}
        # else: a non-sf command with sf only as data (grep sf, echo 'sf ...') → allowed
    # an org-alias/config/login mutation in the SAME command as a write can re-point the write's
    # target between this check and execution (TOCTOU, audit TQ-002) — refuse the combination
    if mutations and writes:
        return {"deny": ("a Salesforce alias/config/login change combined with a write in one "
                         "command can re-point the target — run them separately", "mutate-then-write")}
    return {"deny": None, "writes": writes}


# ---- MCP surface (shared, TRUE default-deny) ------------------------------
MCP_ORG_KEYS = ("targetOrg", "target-org", "targetusername", "username", "usernameOrAlias", "org",
                "alias", "orgId", "orgid", "connection", "instanceUrl", "instanceurl", "targetOrgId")
MCP_WRITE_LEADS = {"deploy", "delete", "create", "update", "upsert", "assign", "execute", "purge",
                   "destroy", "remove", "drop", "truncate", "erase", "install", "uninstall",
                   "import", "merge", "undelete", "activate", "deactivate", "enable", "disable",
                   "publish", "submit", "approve", "reject", "convert", "restore", "schedule",
                   "promote", "refresh", "set", "add", "modify", "insert", "write", "complete",
                   "cancel", "abort", "quick", "undeploy", "apex", "anonymous"}
MCP_READ_LEADS = {"query", "get", "list", "describe", "retrieve", "read", "search", "preview",
                  "display", "overview", "status", "count", "fetch", "show", "find", "explain",
                  "inspect", "view", "check", "lookup", "soql", "tooling", "schema"}


def mcp_target(tinput):
    return next((tinput.get(k) for k in MCP_ORG_KEYS if tinput.get(k)), None)


def _mcp_destructive(name, tinput):
    # component-matched (so `get_settings` doesn't match `set`, audit T12-03)
    comps = set(re.split(r"[_-]", name))
    no_id = not (tinput.get("recordId") or tinput.get("id") or tinput.get("record-id"))
    if "apex" in comps or "anonymous" in comps or "apex" in name:
        body = tinput.get("apexCode") or tinput.get("apex") or tinput.get("code") or ""
        return ("apex", hashlib.sha256(body.encode()).hexdigest()[:16], body)
    if comps & {"delete", "purge", "destroy", "remove", "erase", "truncate", "drop"}:
        if comps & {"bulk", "hard", "all", "mass"}:
            return ("bulk-delete", "", str(tinput))
        return ("where-delete", "", str(tinput)) if no_id else None
    if comps & {"bulk", "import"}:
        return ("bulk-write", "", str(tinput))
    if (comps & {"update", "upsert", "modify", "patch", "set", "write", "edit"}) and no_id:
        return ("where-update", "", str(tinput))
    if "deploy" in comps and (tinput.get("preDestructiveChanges") or tinput.get("postDestructiveChanges")
                              or tinput.get("pre-destructive-changes") or tinput.get("post-destructive-changes")):
        return ("destructive-metadata", "", str(tinput))
    return None


def mcp_analyze(tool, tinput):
    """{'read':True} | {'write':target, 'destructive':(op,digest,body)|None}. TRUE default-deny:
    an org-touching tool that is not clearly a read is a write (audit R11-04/R11-05)."""
    name = tool.split("__")[-1].lower()
    target = mcp_target(tinput)
    comps = set(re.split(r"[_-]", name))
    dest = _mcp_destructive(name, tinput)
    # ANY write verb anywhere in the name makes it a write (get_or_create_record, audit TQ-008)
    writeish = bool(comps & MCP_WRITE_LEADS) or "apex" in name or "anonymous" in name
    readish = bool(comps & MCP_READ_LEADS) or name.startswith(("soql", "tooling", "schema"))
    if writeish or dest:
        return {"write": target, "destructive": dest}
    if readish and not target:
        return {"read": True}
    if readish and target:
        return {"read": True}                          # a named read with an org param is a read
    if target is not None:
        return {"write": target, "destructive": None}  # org-touching, unknown verb ⇒ default-deny
    return {"read": True}                               # no org, not writeish ⇒ local tool
