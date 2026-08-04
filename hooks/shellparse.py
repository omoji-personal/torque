"""Shared shell + Salesforce-CLI analysis for the Torque gates.

ONE classifier, used by BOTH prod_write_gate and destructive_data_gate, so the two hooks can
never disagree about whether a command runs `sf`. Rounds 10–11 (codex shell-semantics, kimi
executed-vs-real-sf, Claude hostile-qa) proved the gates must decide "is this sf, and which
subcommand" from parsed argv — never raw-text regex — and must FAIL CLOSED on any indirection
that could reach `sf` but cannot be statically resolved. Every bypass found routed AROUND the
sf-base0 classifier (relocation under a runner, glued redirect, legacy colon syntax, MCP
naming), so the fixes harden those seams while keeping direct-`sf` authorization intact.
"""
import os, re, shlex, hashlib, fnmatch, json, shutil
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
# Line editors: their file operand is written, not merely read.
EDITOR_CMDS = {"ed", "ex", "red", "vi", "vim", "nvim", "emacs"}
_PY_BINS = {"python", "python2", "python3"}
_WRITE_VOCAB = WRITE_SHAPE_CMDS | PERM_CMDS | EDITOR_CMDS | {"sed"}
CD_FLAGS = {"-P", "-L", "-e", "-@", "-"}
SF_BINS = {"sf", "sfdx"}

_GLOB_META = re.compile(r"[*?\[]")


def bash_glob(pat: str) -> str:
    """Translate a bash glob into the dialect `fnmatch` actually speaks.

    bash accepts BOTH `[!x]` and `[^x]` to negate a bracket expression. fnmatch accepts only
    `[!x]` and reads `[^x]` as the literal set {^, x}. So `s[^z]` — which bash expands to `sf` —
    did not match `sf` under fnmatch, and survived the fix that closed the other five glob
    spellings: 20 of 24 bypass pairs closed and four stayed open, in the round that was meant to
    close all of them. Caught only because the sweep re-ran against the patched parser instead
    of trusting the patch.

    Applied on every path where a shell-authored pattern meets fnmatch, so the two dialects
    cannot disagree again.
    """
    return re.sub(r"\[\^", "[!", pat or "")


def cmd_base(word, vocab=None):
    """Basename of a command WORD, resolved through shell globbing.

    The operand side of this parser has expanded globs since the first audit — `_protected_path`
    matches `settings.jso*` against `settings.json` a few hundred lines below. The command-word
    side never learned the same lesson, and dispatched on the literal string. So `/bin/r[m]`,
    `/usr/local/bin/s[f]` and `/bin/d[d]` — which bash resolves to rm, sf and dd — were read as
    the words `r[m]`, `s[f]`, `d[d]`, matched no vocabulary, and every gate returned ALLOW.

    That was one assumption ("the command word is a literal") held on one side of the parser and
    not the other, and it cost everything: measured at the gates, `/usr/local/bin/s[f] data delete
    record --target-org <prod>` reached the org unadjudicated, as did bulk delete, production
    deploy and anonymous Apex; and `/bin/r[m] -rf hooks` deleted the gates' own source. Six
    spellings — [...], [^...], [!...], [a-z], ?, * — against every watched binary. Braces were
    already handled, which is what made the gap hard to see: `s{e,f}` denied and `s[f]` did not.

    Matching is by PATTERN, not by filesystem: a word whose basename could name a watched command
    is treated as that command whether or not such a file exists on this machine. That fails
    closed, costs at most a deny the operator can lift, and does not vary by host.
    """
    base = os.path.basename(word or "").lower()
    if not vocab:
        return base
    # Braces are expanded a layer above this, in the segment splitter, so `s{e,f}` was already
    # denied end to end. Handling them here as well costs one loop and makes this function's
    # answer complete on its own — a helper whose contract is half-delegated is how a caller
    # that skips the other layer ends up trusting a wrong answer.
    cands = [base] + ([os.path.basename(a).lower() for a in _brace_expand(base)]
                      if "{" in base else [])
    for cand in cands:
        if cand in vocab:
            return cand
        if _GLOB_META.search(cand):
            for name in sorted(vocab):
                if fnmatch.fnmatch(name, bash_glob(cand)):
                    return name
    return base


REDIR_FUSED = re.compile(r"^(?:\{\w+\}|&|\d+)?<?>{1,2}[|!&]?(.*)$")  # > >> 1> 2<> &> >| >& {fd}> >! (+ glued path)
PROTECTED_BASENAMES = {"lib.py", "shellparse.py", "prod_write_gate.py", "destructive_data_gate.py",
                       "lib_cli.py", "settings.json", "writable-orgs.json", "protected-objects",
                       "cli-write-surface.json", "clean-ip.rules", ".classify-cache.json",
                       # decides which checks the agent may run against a live org
                       "read-only-checks.json",
                       "audit.log", "torque-approve", "torque-frontdoor", "torque-install-gates",
                       "validate.py",
                       # The catalogue and the alias index feed regular expressions into the
                       # gate's own note rendering, and the per-org store feeds it text. They
                       # were writable through the agent's Write tool, which made a courtesy
                       # feature into an input the agent controls on the path of a blocking
                       # decision (release panel, codex/gpt-5.6-sol).
                       "salesforce-platform.yml", ".alias-index.json"}

# Directories the agent may not write into for the same reason: their contents are read by the
# gate. Basename matching is not enough here — a per-org file is named after an org Id.
PROTECTED_DIRS = ("knowledge/", "local/orgs/", "bin/", "hooks/")


def stages_local(argv) -> bool:
    """True for a git command that would put a `local/` path into the index.

    `local/` holds per-org findings, session logs with before/after record values, and the audit
    log. It is gitignored, and `git add -f` overrides that in one flag. "Gitignored, 0600, never
    leaves the machine" is only true while nothing can stage it (release panel, codex/gpt-5.6-sol).
    """
    if not argv or cmd_base(argv[0], {"git"}) != "git":
        return False
    verbs = {"add", "stage", "commit", "rm"}
    if not any(a in verbs for a in argv[1:4]):
        return False
    return any("local/" in a or a.rstrip("/").endswith("local") for a in argv[1:])


# Trust is a property of what a tool DOES, not of where it lives. Naming each read-only tool
# is more maintenance than a path rule and that is the point: a new tool under bin/ gets no
# trust until someone decides it deserves some.
READ_ONLY_FIRST_PARTY = {"torque-checkup", "torque-blast-radius", "torque-log", "torque-done",
                         "torque-receipt", "torque-needs"}
READ_ONLY_DISPATCH = {"checkup", "blast-radius", "log", "done", "receipt", "needs"}

# Legacy sfdx command IDs and the modern `sf` words that mean the same operation.
#
# The gate has always authorized both spellings — classify_destructive pairs each modern shape
# with its `force:` twin, one `or` at a time. The CATALOGUE never learned the legacy half, and
# its triggers are written against modern text, so `sf force:data:bulk:delete -s Log__c -f
# ids.csv` reached ZERO catalogue entries while `sf data delete bulk --sobject Log__c --file
# ids.csv` reached four. Measured across five operation pairs: legacy reached nothing, five
# times out of five. An operator on the older syntax was correctly gated and told nothing about
# what they were doing.
#
# The table lives here rather than in lib because this module owns the CLI vocabulary, and a
# second copy of it next to the notes engine is the defect it exists to fix.
# `legacy_map_covers_the_classifier` fails the build when a `force:` prefix the classifier knows
# about is missing here, so the two cannot drift apart quietly.
LEGACY_TO_MODERN = {
    "force:data:bulk:delete": "data delete bulk",
    "force:data:bulk:upsert": "data upsert bulk",
    "force:data:bulk:update": "data update bulk",
    "force:data:bulk:status": "data bulk results",
    "force:data:record:create": "data create record",
    "force:data:record:update": "data update record",
    "force:data:record:delete": "data delete record",
    "force:data:record:get": "data get record",
    "force:data:soql:query": "data query",
    "force:data:tree:import": "data import tree",
    "force:data:tree:export": "data export tree",
    "force:source:deploy": "project deploy start",
    "force:source:retrieve": "project retrieve start",
    "force:source:delete": "project delete source",
    "force:mdapi:deploy": "project deploy start",
    "force:mdapi:retrieve": "project retrieve start",
    "force:apex:execute": "apex run",
    "force:apex:test:run": "apex run test",
    "force:apex:log:get": "apex get log",
    "force:apex:class:list": "apex list class",
    "force:org:display": "org display",
    "force:org:list": "org list",
    "force:org:open": "org open",
    "force:org:create": "org create scratch",
    "force:org:delete": "org delete scratch",
    "force:schema:sobject:describe": "sobject describe",
    "force:schema:sobject:list": "sobject list",
    "force:package:installed:list": "package installed list",
    "force:api:request:rest": "api request rest",
}


def modernize(command: str) -> str:
    """The command with every legacy ID replaced by its modern words.

    For MATCHING ONLY — notes, requirements, retrieval. Nothing authorizes from this. The
    authorization path reads argv and knows both spellings already; this exists so knowledge
    written against one spelling reaches an operator using the other.
    """
    if not command or "force:" not in command:
        return command
    out = command
    # longest first, so force:apex:test:run is not eaten by a shorter prefix
    for legacy in sorted(LEGACY_TO_MODERN, key=len, reverse=True):
        out = out.replace(legacy, LEGACY_TO_MODERN[legacy])
    return out


def _is_own_harness(argv) -> bool:
    """True only for first-party tools explicitly declared READ-ONLY, under TORQUE_HOME.

    The interpreter rule refuses `python3 …` when a Salesforce target is present, because an
    interpreter can do anything opaquely. That correctly refused `python3 bin/torque checkup
    --target-org X` and `blast-radius` — two first-party, READ-ONLY commands the guide tells
    operators to run. A gate that refuses the tool's own documented commands is a gate people
    route around, which costs more safety than it buys.

    It used to trust anything under bin/ whose name began with "torque", plus validate.py. That
    is LOCATION-based trust, and P0-001 was its first bill: torque-shadow was written, landed in
    bin/, inherited the exemption, and handed the agent arbitrary Apex around the anonymous-Apex
    control. The path rule granted trust to a tool nobody had decided to trust (P1-002, external
    audit).

    So trust is now CAPABILITY-based: a named set of tools that only read. validate.py loses the
    exemption deliberately — probe_cycle deploys and deletes metadata, so it was never read-only
    and never should have carried a read-only exemption. The consequence is real and accepted:
    an operator runs the harness themselves rather than an agent running it through this gate.

    The file must still resolve inside TORQUE_HOME, and bin/ is in PROTECTED_DIRS so the agent
    cannot write the file it would then be allowed to run. That equivalence still matters and
    bin_is_protected still asserts the pair — it is just no longer the whole argument.
    """
    if len(argv) < 2 or cmd_base(argv[0], _PY_BINS).split(".")[0] not in _PY_BINS:
        return False
    try:
        home = lib.TORQUE_HOME.resolve()
        cand = Path(argv[1])
        cand = (cand if cand.is_absolute() else (lib.TORQUE_HOME / cand)).resolve()
        # The harness sits in harness/, not bin/, and is trusted only one check at a time. Its
        # file is in PROTECTED_BASENAMES, so the same equivalence bin/ relies on holds: the agent
        # cannot write the file it would then be allowed to run.
        if cand.parent == (home / "harness") and cand.name == "validate.py":
            return _harness_check_is_read_only(argv[2:], home)
        if cand.parent != (home / "bin"):
            return False
        if cand.name == "torque":                       # the dispatcher: only its read-only verbs
            return len(argv) > 2 and argv[2] in READ_ONLY_DISPATCH
        return cand.name in READ_ONLY_FIRST_PARTY
    except Exception:
        return False


def _harness_check_is_read_only(rest, home) -> bool:
    """`validate.py --only <check>` where THAT CHECK is declared to make no org mutation.

    The harness as a whole is not read-only and must never be trusted as if it were — probe_cycle
    deploys and hard-deletes metadata, which is why P1-002 took its exemption away. Most of its
    checks are another matter: describe_first only queries. Refusing the whole tool meant every
    live diagnosis needed the operator to run a full profile and paste it back, which is friction
    against a correctness control and therefore something that eventually gets removed.

    Parsed, not pattern-matched. `--only describe_first --profile release` must NOT walk through
    on the strength of containing a permitted name, and neither must a second `--only`, an
    `--only` whose value is absent, or `--self-test` riding alongside. Anything this cannot
    resolve exactly is refused, which is the same posture the rest of this module takes toward
    argv it cannot statically settle.
    """
    try:
        names = set(json.loads((home / "harness" / "checks" /
                                "read-only-checks.json").read_text())["checks"])
    except Exception:
        return False                                    # no manifest, no exemption
    only, seen = None, 0
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--self-test" or tok.startswith("--self-test="):
            return False                                # runs mutators, and one needs an org
        if tok == "--allow-skip" or tok.startswith("--allow-skip="):
            return False                                # degrading a run is the operator's call
        if tok == "--only":
            seen += 1
            if i + 1 >= len(rest):
                return False                            # a flag with no value settles nothing
            only = rest[i + 1]
            i += 2
            continue
        if tok.startswith("--only="):
            seen += 1
            only = tok.split("=", 1)[1]
        i += 1
    if seen != 1 or not only:
        return False                                    # exactly one check, named exactly once
    return only in names


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


_GROUP_PRECEDED_BY = (" ", "\t", "\n", ";", "&", "|", "")


def grouping_or_subst(cmd: str) -> bool:
    """Shell grouping or substitution — over the UNQUOTED regions only.

    This was a raw text scan: `"$(" in cmd`, plus a regex for a standalone `(` or `{`, with no
    idea where the quotes were — while split_segments, forty lines above, tracks quote state
    correctly. The machinery to do this right was already in the file and this function did not
    use it. A parenthesis inside a single-quoted SOQL string therefore read as shell grouping,
    and bash expands nothing whatsoever inside single quotes.

    What that cost, measured rather than imagined: replaying six months of real client-work
    commands (1,193 Salesforce CLI invocations) through this classifier denied 80.1% of them,
    and 854 of those 955 denials were this one defect. `WHERE Id IN ('a','b')` — denied.
    `WHERE Name = 'Acme (US)'` — denied. `SELECT Id, (SELECT Id FROM Contacts) FROM Account`,
    a relationship subquery and the commonest SOQL idiom after a plain select — denied.

    193 gate fixtures did not catch it, and 44 of those assert `allow`, so the must-allow
    direction existed. What it did not contain was SOQL: its entire query vocabulary was four
    instances of `SELECT Id FROM Account`, with no WHERE clause, no IN list, no subquery and no
    quoted literal. Hand-imagined allow cases were a narrower distribution than real use, which
    is why a corpus of real commands found in one pass what the suite could not.

    The rule below is bash's own semantics, not a loosening:
      outside quotes   $(  `  <(  >(  and a standalone (  or {
      inside "..."     $(  `        — expansion still happens in double quotes
      inside '...'     nothing      — single quotes are literal, bash expands nothing
    """
    i, n, quote = 0, len(cmd), None
    while i < n:
        c = cmd[i]
        if quote == "'":
            if c == "'":
                quote = None
            i += 1
            continue
        if quote == '"':
            if c == "\\" and i + 1 < n:            # backslash escapes inside double quotes
                i += 2
                continue
            if c == "`" or cmd[i:i + 2] == "$(":
                return True
            if c == '"':
                quote = None
            i += 1
            continue
        if c == "\\" and i + 1 < n:                # an escaped quote does not open a region
            i += 2
            continue
        if c in ("'", '"'):
            quote = c
            i += 1
            continue
        if c == "`" or cmd[i:i + 2] in ("$(", "<(", ">("):
            return True
        if c in ("(", "{") and (cmd[i - 1] if i else "") in _GROUP_PRECEDED_BY:
            return True
        i += 1
    # An unterminated quote is not a safe parse, and returning False here grants nothing:
    # split_segments reports the imbalance separately and analyze_bash denies on it first.
    return False


_BRACE = re.compile(r"\{([^{}]*,[^{}]*)\}")
# bash's OTHER brace form: a sequence expression, which has no comma and so matched nothing
# above. `sf` was reachable as `s{e..f}` and the signing secret as `~/.torq{t..v}e/secret`.
# On the operand side this was masked by the directory-prefix rule catching the same paths a
# second way — the same redundancy that hid three case-sensitivity defects, doing it again.
_BRACE_SEQ = re.compile(r"\{(-?\d+)\.\.(-?\d+)(?:\.\.(-?\d+))?\}|\{([A-Za-z])\.\.([A-Za-z])(?:\.\.(-?\d+))?\}")
_BRACE_CAP = 64          # fail CLOSED above this: a cross-product bomb must not buy an allow


def _seq_alts(m):
    """The alternatives of one bash sequence expression, or None if it is wider than the cap.

    Returning None rather than the list is what keeps `{1..100000000}` from being expanded
    before the caller's cap can notice — generating the bomb and then measuring it is how a
    guard that fails closed on paper hangs in practice.
    """
    lo, hi, step, alo, ahi, astep = m.groups()
    if lo is not None:
        a, b = int(lo), int(hi)
        st = abs(int(step)) if step else 1
        width = max(len(lo.lstrip("-")), len(hi.lstrip("-"))) if (
            lo.lstrip("-").startswith("0") or hi.lstrip("-").startswith("0")) else 0
        if st == 0 or abs(b - a) // st + 1 > _BRACE_CAP:
            return None
        rng = range(a, b + 1, st) if b >= a else range(a, b - 1, -st)
        return [f"{v:0{width}d}" if width else str(v) for v in rng]
    a, b = ord(alo), ord(ahi)
    st = abs(int(astep)) if astep else 1
    if st == 0 or abs(b - a) // st + 1 > _BRACE_CAP:
        return None
    return [chr(v) for v in (range(a, b + 1, st) if b >= a else range(a, b - 1, -st))]


def _brace_expand(tok):
    """Bash brace expansion, which happens BEFORE glob expansion and which the gate must model.
    `~/.torq{u,x}e/secret` becomes two paths, one of which is the signing secret; without this
    the token matched no protected pattern and was allowed (audit: red-team P0-2).
    Returns every alternative, or the sentinel '**' form when the cross-product is too large,
    so an over-wide brace can never be cheaper than a plain path."""
    out = [tok]
    for _ in range(8):                                  # nested braces, bounded
        nxt = []
        for cur in out:
            m = _BRACE.search(cur)
            if m:
                for alt in m.group(1).split(","):
                    nxt.append(cur[:m.start()] + alt + cur[m.end():])
                continue
            s = _BRACE_SEQ.search(cur)
            if not s:
                nxt.append(cur); continue
            alts = _seq_alts(s)
            if alts is None:                            # wider than the cap ⇒ collapse, not expand
                return [_BRACE_SEQ.sub("*", _BRACE.sub("*", tok))]
            for alt in alts:
                nxt.append(cur[:s.start()] + alt + cur[s.end():])
        if nxt == out:
            break
        out = nxt
        if len(out) > _BRACE_CAP:
            # Collapse BOTH brace forms. Substituting only the comma form left a sequence-only
            # token (`hooks/lib.p{a..z}{a..z}`) literal, so the over-wide case — the one the cap
            # exists for — returned something that matches no protected pattern. The cap was
            # fail-open in exactly the situation it was written to fail closed on.
            return [_BRACE_SEQ.sub("*", _BRACE.sub("*", tok))]
    return out or [tok]


def _abs_pattern(tok, cwd=None, varmap=None):
    """A token as an ABSOLUTE glob PATTERN, expansion-aware: ~ expanded, $var/${}/backtick → '*'
    (any shell var could construct any path), relative made absolute against cwd. The gate sees
    PRE-EXPANSION text; bash expands globs/vars AFTER the hook (audit T12-01/02), so trust
    decisions must be made on what the token COULD become, not its literal form."""
    # Resolve command-local vars FIRST (inline `p=$HOME/$d$e` assignments — a single Bash call is
    # a fresh shell, so every $var is either inline-assigned or an env/profile var, audit TQ-F1),
    # then ~ , then env vars, then any still-undefined var → empty (bash semantics). Backtick
    # command-substitution → ** (its output is unknown; the substitution guard denies sf ones).
    t = _sub_vars(tok, varmap or {})
    t = os.path.expanduser(t)
    t = os.path.expandvars(t)
    t = re.sub(r"\$\{\w+\}|\$\w+", "", t)             # undefined var → empty, like bash
    t = re.sub(r"`[^`]*`", "**", t)
    if not os.path.isabs(t):
        t = os.path.join(str(cwd if cwd is not None else _safe_cwd()), t)
    # realpath (not normpath) so a symlinked prefix (macOS /tmp→/private/tmp) matches the
    # anchor's own .resolve(); wildcard components that don't exist are left intact (audit R11-10)
    return os.path.realpath(t)


def _abs_patterns(tok, cwd=None, varmap=None):
    """Every absolute pattern a token could become, once brace expansion is accounted for.
    Callers that make a trust decision must consider ALL of them; reaching on any one denies."""
    return [_abs_pattern(alt, cwd, varmap) for alt in _brace_expand(tok)]


def _sub_vars(s, varmap):
    def rep(m):
        v = m.group(1) or m.group(2)
        return varmap[v] if v in varmap else m.group(0)   # leave env/undefined for the next stages
    return re.sub(r"\$\{(\w+)\}|\$(\w+)", rep, s)


def _command_vars(cmd):
    """Map of VAR→resolved value for every `VAR=value` assignment in the command, resolved in
    order against earlier vars + env, so an inline var holding an absolute path is known when a
    later token uses it (audit TQ-F1)."""
    varmap = {}
    for m in re.finditer(r"(?:^|[;\s&|(])(\w+)=(\"[^\"]*\"|'[^']*'|[^;\s|&()]*)", cmd):
        name, val = m.group(1), m.group(2)
        if val[:1] in ("\"", "'"):
            val = val[1:-1]
        val = _sub_vars(val, varmap)
        val = os.path.expandvars(val)
        varmap[name] = val
    return varmap


def _safe_cwd():
    try:
        return os.getcwd()
    except Exception:
        return os.environ.get("TORQUE_HOME", "/")


def _glob_reaches(pat_parts, tgt_parts):
    """True if a glob path (pat_parts) could match a path AT or UNDER tgt_parts — i.e. some PREFIX
    of the glob consumes ALL of tgt. `**` matches ZERO OR MORE components; `*`/`?`/`[...]` match
    within one component via fnmatch. A positional compare (the prior version) misaligned on `**`
    and let `/Users/**/.../.[t]orque/sec[r]et` slip (audit round 13). This is a proper glob match."""
    # case-fold: APFS/HFS+ are case-insensitive by default, so `.SFDX` and `.sfdx` are one
    # file. Over-matching is fail-closed, hence applied unconditionally.
    pat_parts = [x.lower() for x in pat_parts]
    tgt_parts = [x.lower() for x in tgt_parts]
    m, n = len(pat_parts), len(tgt_parts)
    dp = [[False] * (n + 1) for _ in range(m + 1)]
    dp[0][0] = True
    for i in range(1, m + 1):
        p = pat_parts[i - 1]
        if p == "**":
            dp[i][0] = dp[i - 1][0]
            for j in range(1, n + 1):
                dp[i][j] = dp[i - 1][j] or dp[i][j - 1]   # `**` consumes 0 or ≥1 components
        else:
            for j in range(1, n + 1):
                if fnmatch.fnmatch(tgt_parts[j - 1], p):
                    dp[i][j] = dp[i - 1][j - 1]
    return any(dp[i][n] for i in range(m + 1))


def _pattern_reaches_dir(pat, dirpath):
    return _glob_reaches(pat.split(os.sep), dirpath.split(os.sep))


def normalize_separators(tok: str) -> str:
    """Treat a backslash as a path separator for GUARD purposes.

    Three guards — anchor_ref, sf_auth_ref and lib.is_protected_target — each independently
    assumed a forward slash. `hooks\\lib.py` is the same file as `hooks/lib.py` on Windows, and
    on POSIX it is a filename containing a literal backslash, which nobody legitimately has under
    these directories. Reading it as a separator therefore fails safe on one platform and costs
    nothing on the other.

    A single shared assumption across three guards that are not copies of each other is the shape
    that produced the case-sensitivity bypass; found by sweeping for it rather than by waiting
    for an audit to trip over it.
    """
    return (tok or "").replace("\\", "/")


def anchor_ref(tok, cwd=None, varmap=None) -> bool:
    """Deny any reference to the trust anchor EXCEPT a read of the approved-apex copy — resolved
    against the ACTUAL anchor paths AND expansion-aware, so `cat ~/.torq*/secret`,
    `a=.tor;b=que;cat ~/$a$b/secret`, `cat /Users/**/.../.[t]orque/sec[r]et`, and a custom
    TORQUE_ANCHOR all deny (audit T12-01 / round 13)."""
    tok = normalize_separators(tok)
    if "{" in tok and "," in tok:            # brace expansion precedes globbing
        _alts = _brace_expand(tok)
        if len(_alts) > 1 or (_alts and _alts[0] != tok):   # only if it EXPANDED
            return any(anchor_ref(_a, cwd, varmap) for _a in _alts)
    pat = _abs_pattern(tok, cwd, varmap)
    approved = str(lib.APPROVED.resolve())
    if pat == approved or pat.startswith(approved + os.sep):
        return False                                  # the approved-apex copy is readable
    if re.search(r"(^|/)\.torque(/|$)", tok.lower()) and "/approved/" not in tok.lower():
        return True                                   # `.torque` as a COMPONENT (not `.torquerc`)
    if _glob_reaches(pat.split(os.sep), str(lib.SECRET.resolve()).split(os.sep)):
        return True                                   # the secret file, reached by any glob/**/char-class
    if _pattern_reaches_dir(pat, str(lib.ANCHOR.resolve())):
        return True                                   # anything at/under the anchor dir (tokens, grants)
    return False


def sf_auth_ref(tok, varmap=None) -> bool:
    """The sf CLI auth store (~/.sfdx, ~/.sf) holds live access tokens — an agent reading it via
    Bash (`cat ~/.sfd*/x.json`) could lift a token and curl the REST API, bypassing sf entirely.
    Expansion-aware, same as anchor_ref (audit T12-01 applied to the auth store)."""
    tok = normalize_separators(tok)
    if "{" in tok and "," in tok:
        _alts = _brace_expand(tok)
        if len(_alts) > 1 or (_alts and _alts[0] != tok):
            return any(sf_auth_ref(_a, varmap) for _a in _alts)
    pat = _abs_pattern(tok, None, varmap)
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


def _api_method_glued(argv):
    """`-XPOST` glued, which curl-style short flags allow and the separate-form
    parser missed."""
    for a in argv:
        if a.startswith("-X") and len(a) > 2:
            return a[2:].upper()
    return None


def _api_method(sf_args):
    args = _cut_ddash(sf_args)
    for i, a in enumerate(args):
        if a in ("-X", "--method") and i + 1 < len(args):
            return args[i + 1].upper()
        if a.startswith("--method="):
            return a.split("=", 1)[1].upper()
    return "GET"


def flag_value(sf_args, *names):
    """The value of a long flag, in the separated and equals forms oclif accepts.

    Used to recover the criteria an impact-bound approval was computed from, so the gate can
    re-establish the scope. It reads only; it never decides on its own.
    """
    args = _cut_ddash(sf_args)
    for i, a in enumerate(args):
        if a in names and i + 1 < len(args):
            return args[i + 1]
        for n in names:
            if a.startswith(n + "="):
                return a[len(n) + 1:]
    return ""


def sobject_value(sf_args):
    """The sObject an operation targets, in every flag form oclif accepts.

    Glued short flags (`-sAccount`) are ordinary oclif syntax and were NOT extracted, so the
    protected-object shield could not see the object name. That mattered only in the one case
    where the shield is the last line of defence: an operator has already issued a valid
    bulk-delete token, which is not object-scoped, and the shield is what still refuses
    Account. Found by the external panel (antigravity/gemini-3.1-pro) — its own evidence used
    a flag that does not exist, but the reasoning held for the glued form, which does.
    """
    args = _cut_ddash(sf_args)
    for i, a in enumerate(args):
        if a in ("--sobject", "-s", "--sobjecttype", "--sobjecttypecategory") and i + 1 < len(args):
            return args[i + 1]
        for f in ("--sobject=", "-s=", "--sobjecttype=", "--sobject-type=", "--sobjectType="):
            if a.startswith(f):
                return a[len(f):]
        if len(a) > 2 and a.startswith("-s") and not a.startswith("--"):
            return a[2:]                       # glued short flag: -sAccount
    return None


# Top-level `sf` topics. The verb is found by anchoring on one of these rather than by assuming
# it comes first, because oclif accepts global and command flags BEFORE the verb.
SF_TOPICS = {"data", "apex", "org", "project", "api", "package", "sobject", "schema", "alias",
             "config", "auth", "user", "lightning", "visualforce", "community", "limits",
             "doctor", "plugins", "autocomplete", "which", "search", "info", "agent"}


def _topic_anchored(args):
    """Positionals from the first recognised sf topic onward, ignoring flags and their values.
    Returns () when no topic is present."""
    pos = []
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("-"):
            # a flag; if it takes a separate value, that value is not a positional
            if "=" not in a and i + 1 < len(args) and not args[i + 1].startswith("-"):
                i += 2
                continue
            i += 1
            continue
        pos.append(a.lower())
        i += 1
    for idx, tok in enumerate(pos):
        head = tok.split(":", 1)[0]
        if head in SF_TOPICS or tok.startswith("force:"):
            return tuple(pos[idx:])
    return ()


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


def _normalize_subcommand(sub):
    """Split modern colon-joined command IDs into the space form oclif treats as equivalent.

    `sf data:delete:bulk` == `sf data delete bulk` (verified: `sf data:delete:bulk --help` exits
    0). Legacy `force:` IDs are matched elsewhere as whole strings and must NOT be split here,
    or `force:data:bulk:delete` would stop matching its own rule.
    """
    out = []
    for tok in sub:
        if ":" in tok and not tok.startswith("force:"):
            out.extend(p for p in tok.split(":") if p)
        else:
            out.append(tok)
    return tuple(out)


def _subcommand_is_opaque(sub, sf_args):
    """True when the verb cannot be read off the command line at all — a shell expansion supplied
    it (`sf "$@"`, `sf data $V ...`). The gate cannot classify what it cannot see, and an
    unclassified sf WRITE must never be cheaper than a classified one."""
    return any("$" in t or "`" in t or "{" in t for t in tuple(sub) + tuple(sf_args))


# Words that mean "this removes or overwrites something", tested against the RESOLVED command id
# only after every precise rule has declined. This is the safety net for verbs that do not exist
# yet — not a default-deny, which would tax every harmless unknown.
_DESTRUCTIVE_WORDS = ("delete", "destroy", "purge", "erase", "truncate", "drop", "wipe",
                      "nuke", "uninstall", "revoke", "reset")


# (topic, verb) pairs the precise rules above already reason about. For these the specific
# logic has ALREADY decided — including deciding that something is fine, like a bounded delete
# by record id, which is deliberately free. The shape net must not second-guess a rule that
# looked at the command and said yes.
_PRECISELY_HANDLED = {
    ("data", "delete"), ("data", "update"), ("data", "upsert"), ("data", "import"),
    ("data", "export"), ("data", "query"), ("data", "get"), ("data", "create"),
    ("apex", "run"), ("apex", "test"), ("apex", "get"),
    ("org", "delete"), ("org", "create"), ("org", "list"), ("org", "display"),
    ("api", "request"), ("project", "delete"), ("project", "deploy"), ("project", "retrieve"),
}


def _destructive_shape(sub):
    """True when a verb the precise rules do NOT cover still reads as destructive.

    This is the safety net for verbs that do not exist yet — a novel spelling of "remove". It
    is deliberately narrow: it never overrides a precise rule, and it never fires on an unknown
    verb that merely sounds harmless. Charging every unrecognised command a token would be the
    safer-looking choice and the wrong one; a gate that taxes ordinary work gets uninstalled.
    """
    if not sub:
        return False
    if tuple(sub[:2]) in _PRECISELY_HANDLED or sub[0].startswith("force:"):
        return False
    joined = ":".join(sub)
    return any(w in joined for w in _DESTRUCTIVE_WORDS)


_DEPLOY_DIR_FLAGS = ("--metadata-dir", "--source-dir", "-d", "--sourcepath", "--deploydir")
_DESTRUCTIVE_MANIFESTS = ("destructivechanges.xml", "destructivechangespre.xml",
                          "destructivechangespost.xml")


def _deploy_dir_carries_destructive(sub, sf_args) -> bool:
    """Does a deploy DIRECTORY contain a destructiveChanges manifest?

    The Metadata API honours a `destructiveChanges.xml` in the package root with NO flag — that
    is how destructive deploys worked before DX and it still works through `--metadata-dir`. The
    classifier reasoned only about argv, so:

        sf project deploy start --metadata-dir ./mdapi_out --target-org <allowlisted-sandbox>

    with `./mdapi_out/destructiveChanges.xml` on disk classified NOT DESTRUCTIVE and proceeded
    with no destructive-class token, against a documented contract that says one is required. On
    production the write is still refused by prod_write_gate, so this was never a production
    hole; on an allowlisted sandbox it deleted metadata untokened, and `purgeOnDelete` in that
    manifest hard-deletes. Found by an external audit lens, reproduced before being believed.

    This is the ONLY place the classifier reads the filesystem, and it does so because the
    operation's destructiveness genuinely is not in its argv.

    KNOWN LIMIT, stated rather than hidden: a relative directory is resolved against this
    process's cwd, which is the session's for a Bash hook and TORQUE_HOME for the shim. If it
    does not resolve, this returns False rather than claiming destructiveness it cannot see —
    the deploy still faces ordinary write authorization (allowlist + live non-production check),
    but it will not be charged a destructive token. Failing closed here would demand a token for
    every deploy whose directory this process cannot locate, which is how a gate becomes the
    thing people uninstall.
    """
    if not (sub[:3] == ("project", "deploy", "start")
            or sub[0].startswith(("force:mdapi:deploy", "force:source:deploy"))):
        return False
    dirs = []
    for i, a in enumerate(sf_args):
        if a in _DEPLOY_DIR_FLAGS and i + 1 < len(sf_args):
            dirs.append(sf_args[i + 1])
        elif a.startswith(tuple(f + "=" for f in _DEPLOY_DIR_FLAGS)):
            dirs.append(a.split("=", 1)[1])
    for d in dirs:
        try:
            p = Path(d).expanduser()
            if not p.is_dir():
                continue
            for entry in p.iterdir():
                if entry.name.lower() in _DESTRUCTIVE_MANIFESTS:
                    return True
        except OSError:
            # The directory exists and cannot be read. That is the one case where failing closed
            # costs nothing an operator would miss: an unreadable deploy source is broken anyway.
            return True
    return False


def classify_destructive(sf_args):
    """Op-class if the parsed sf write is destructive, else None. Covers modern space syntax AND
    legacy colon syntax (audit R11-04/R11-05) and async-resume completion of bulk jobs."""
    sub = subcommand(sf_args)
    if not sub or sub[0].split(":", 1)[0] not in SF_TOPICS and not sub[0].startswith("force:"):
        # The verb was not where we looked: a flag before it (`sf -o org data delete bulk`) makes
        # subcommand() stop early, and a naive re-scan then treats the flag's VALUE as the first
        # positional. Anchor on the topic instead.
        anchored = _topic_anchored(_cut_ddash(sf_args))
        if anchored:
            sub = anchored
    if not sub:
        # nothing recognisable at all — re-scan ALL positionals and fail CLOSED (T12-04)
        sub = tuple(a.lower() for a in sf_args if not a.startswith("-"))
        if not sub:
            return None
    # An expansion-supplied verb is unreadable, so it is treated as the most dangerous thing it
    # could be rather than as harmless (red-team P0-2: `sf "$@"` and `sf data $V` both ran a bulk
    # delete with no token because the classifier saw an unmatched token and returned None).
    if _subcommand_is_opaque(sub, sf_args):
        return "opaque-write"
    sub = _normalize_subcommand(sub)
    f = sub[0]
    if sub[:2] == ("apex", "run") or f.startswith("force:apex:execute"):
        return "apex"                                 # NOT force:apex:test:run (that's a test, TQ-011)
    if sub[:2] == ("org", "delete") or f.startswith("force:org:delete"):
        return "org-delete"                           # destroy a sandbox/scratch org (RU-2)
    if (sub[:2] == ("api", "request") or f.startswith("force:api")) \
       and (_api_method_glued(sf_args) or _api_method(sf_args)) in ("POST", "PUT", "PATCH", "DELETE"):   # glued FIRST: _api_method defaults to "GET" when no separate -X is present, so the other
       # order short-circuits on that default and never sees `-XPOST`.
        return "destructive-metadata"                 # raw REST DML bypasses data-* verbs (TQ-F3)
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
              for a in sf_args) or "destructivechanges" in " ".join(sf_args).lower() \
       or _deploy_dir_carries_destructive(sub, sf_args):
        return "destructive-metadata"
    # Nothing precise matched. If the verb still READS as destructive, charge a token;
    # otherwise let it through to the ordinary write authorisation (allowlist + live
    # non-production check). Deliberately NOT a default-deny: taxing every harmless
    # unknown is how a gate becomes the thing people uninstall.
    if _destructive_shape(sub):
        return "unrecognised-destructive"
    return None


def wrapped_sf(argv):
    """True if some token is a standalone sf/sfdx AND the tokens after it look like an sf
    invocation (an sf topic, colon-form, or a target flag). Distinguishes `nice sf data delete`
    (deny) from `grep sf file` / `echo 'sf ...'` (sf is a search/quoted arg — allow)."""
    for i, t in enumerate(argv):
        if cmd_base(t, SF_BINS) in SF_BINS:
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


# Command runners that take a command as their argument. `sf` already handled these via
# wrapped_sf; the write-shape classifiers dispatched on argv[0] alone, so one word in front
# ("nice cp ... hooks/lib.py") bypassed every one of them (audit: red-team P0-1).
RUNNERS = {"env", "nice", "command", "timeout", "nohup", "stdbuf", "ionice", "setsid",
           "caffeinate", "sudo", "doas", "chroot", "unshare", "script", "time", "builtin",
           "exec", "busybox", "xargs"}


def strip_runners(argv):
    """Peel runner prefixes (and their flags / VAR=val assignments) so the classifier sees the
    command that will actually execute. Bounded so a pathological chain cannot spin."""
    argv = list(argv)
    for _ in range(8):
        if not argv or cmd_base(argv[0], RUNNERS) not in RUNNERS:
            break
        runner = cmd_base(argv[0], RUNNERS)
        argv = argv[1:]
        while argv and (argv[0].startswith("-") or re.match(r"^\w+=", argv[0])):
            argv = argv[1:]
        # `timeout 5 tee ...`: the duration is a POSITIONAL, so peeling only flags left "5"
        # as argv[0] and the classifier saw no write shape. A command is never a bare
        # number, so dropping a duration-shaped token cannot swallow a real command.
        if runner == "timeout" and argv and re.match(r"^[0-9]+(\.[0-9]+)?[smhd]?$", argv[0]):
            argv = argv[1:]
    return argv


_SED_W = re.compile(r"(?:^|[;\n}])\s*\d*[,~+]?\d*\s*[wW]\s+(\S+)|s/(?:[^/\\\\]|\\\\.)*/(?:[^/\\\\]|\\\\.)*/[a-z]*w\s+(\S+)")


def _sed_write_targets(argv):
    """Filenames sed will WRITE via its `w`/`W` commands, which need no -i flag.

    `sed -n 'w FILE' input` and `sed 's/a/b/w FILE' input` both create/overwrite FILE. Because
    the filename lives INSIDE the script string rather than in argv, the in-place check never
    saw it and sed was classified as a pure reader.
    """
    out = []
    for a in argv[1:]:
        if a.startswith("-"):
            continue
        for m in _SED_W.finditer(a):
            out.append(m.group(1) or m.group(2))
    return [t for t in out if t]


def _write_shape_targets(argv):
    argv = strip_runners(argv)
    base0 = cmd_base(argv[0], _WRITE_VOCAB) if argv else ""
    cand = []
    if base0 == "sed":
        cand.extend(_sed_write_targets(argv))
    if base0 in EDITOR_CMDS:
        cand.extend(a for a in argv[1:] if not a.startswith("-"))
    for i, tok in enumerate(argv):
        # a redirect operator ANYWHERE in the token: leading fused (`2>path`), OR glued to a
        # preceding word (`printf x>hooks/lib.py` — shlex keeps it one token, audit TQ-F2). The
        # RHS after the last `>` run is the target; a bare trailing `>` → the next token.
        found = False
        for m in re.finditer(r">{1,2}[|!&]?([^>]*?)(?=>|$)", tok):  # EVERY glued target (TQ-F2)
            if m.group(1):
                cand.append(m.group(1)); found = True
        if not found and re.search(r">{1,2}[|!&]?$", tok) and i + 1 < len(argv):
            cand.append(argv[i + 1])                  # bare trailing `>` → next token
    if base0 in WRITE_SHAPE_CMDS or base0 in EDITOR_CMDS or base0 in PERM_CMDS \
           or (base0 == "sed" and (_sed_inplace(argv) or _sed_write_targets(argv))):
        cand += [a for a in argv[1:] if not a.startswith("-")]
        cand += [a.split("=", 1)[1] for a in argv if a.startswith("of=")]
    return base0, cand


def _protected_path(pathlike, cwd=None, varmap=None):
    if not pathlike:
        return False
    # A path that CONTAINS the protected directories is as dangerous as one inside them:
    # `rm -rf .` from the repo root, or `rm -rf <repo>`, destroys every gate file while
    # matching no protected basename and living under no protected prefix. Checking only
    # "is it inside" missed the case where it is "outside and above" (external panel,
    # antigravity/gemini-3.1-pro).
    try:
        _cand = _abs_pattern(pathlike, cwd, varmap).rstrip(os.sep)
        if _cand and _cand != os.sep:
            for _d in lib.protected_write_paths() + [str(lib.TORQUE_HOME.resolve())]:
                if _d == _cand or _d.startswith(_cand + os.sep):
                    return True
    except Exception:
        pass
    if "{" in pathlike and "," in pathlike:
        _alts = _brace_expand(pathlike)
        if len(_alts) > 1 or (_alts and _alts[0] != pathlike):
            return any(_protected_path(_a, cwd, varmap) for _a in _alts)
    pat = _abs_pattern(pathlike, cwd, varmap)
    base = os.path.basename(pat.rstrip("/")) or pat
    # literal OR globbed basename of a distinctive gate file (`settings.jso*` → settings.json)
    for b in PROTECTED_BASENAMES:
        if fnmatch.fnmatch(b, bash_glob(base)) or fnmatch.fnmatch(base, b):
            return True
    if anchor_ref(pathlike, cwd, varmap) or sf_auth_ref(pathlike, varmap):
        return True                                   # incl. `ln -s ~/.sfdx x` symlink source (TQ-F5)
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


# Commands that BUILD a path at runtime, so no literal token ever shows `.torque`/`.sfdx`/a gate
# file and every literal-token guard is blind. `find ~ -name secret -path '*torque*' -exec cat
# {} +` read the signing secret; the same shape with `cp` overwrote a gate file. The action is
# what matters, not the path, so any of these carrying a reader/writer action is refused.
_RUNTIME_PATH_CMDS = {"find", "fd", "locate", "mdfind"}
_RUNTIME_ACTION_FLAGS = ("-exec", "-execdir", "-ok", "-okdir", "-delete",
                         "-fprintf", "-fprint", "-fls")


def runtime_path_action(argv):
    """A find/xargs shape whose target set is computed at run time and then acted on."""
    if not argv:
        return None
    base0 = cmd_base(argv[0], _RUNTIME_PATH_CMDS | {"xargs", "tar"})
    if base0 in _RUNTIME_PATH_CMDS and any(a in _RUNTIME_ACTION_FLAGS for a in argv):
        return base0
    if base0 == "xargs":
        return "xargs"
    if base0 == "tar" and any(a in ("-T", "--files-from") for a in argv):
        return "tar -T"
    return None


def check_write_shapes(segs, varmap=None):
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
        argv = strip_runners(argv)
        if not argv:
            continue
        base0 = cmd_base(argv[0], {"cd", "pushd"})
        if base0 in ("cd", "pushd"):
            rest = [a for a in argv[1:] if a not in CD_FLAGS and not a.startswith("-")]
            if rest:                                    # cd <dir>
                tgt = os.path.expanduser(rest[0])
                newcwd = Path(tgt) if os.path.isabs(tgt) else (cwd / tgt)
                try:
                    r = newcwd.resolve()
                    if lib.is_protected_target(str(r)) or anchor_ref(str(r), varmap=varmap) or ".torque" in str(r).lower():
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
                    if _protected_path(p, cwd, varmap) or _protected_path(p, initial, varmap) \
                       or os.path.basename(p.rstrip("/")) in ("hooks", "bin", ".claude", "checks"):
                        return (f"git {sub} targeting protected paths — refused", "protected-write")
            continue
        _, cands = _write_shape_targets(argv)
        for c in cands:
            # union of tracked cwd AND the real starting cwd — a subshell `(cd x)` cannot
            # persist, so a relative write still resolves against the original dir (audit T12-06)
            if _protected_path(c, cwd, varmap) or _protected_path(c, initial, varmap):
                return (f"write to a protected path: {c}", "protected-write")
    return None


def _is_org_mutation(sf_args):
    """sf subcommands that change the alias/config/auth mapping a later target resolves through."""
    sub = subcommand(sf_args)
    return sub[:2] in (("alias", "set"), ("alias", "unset"), ("config", "set"),
                       ("config", "unset"), ("org", "login"), ("org", "logout"))


def _find_roots(argv):
    """Positional path arguments of a find-like command (everything before the first flag)."""
    roots = []
    for t in argv[1:]:
        if t.startswith("-"):
            break
        roots.append(t)
    return roots or ["."]


def runtime_path_risk(segs, varmap):
    """(producer, reason) when a runtime-constructed path set could reach something protected AND
    something acts on it. `find ~ -name secret -path '*torque*' -exec cat {} +` read the signing
    secret; the same shape piped to `xargs cat` did too, and with `cp` it overwrote a gate file —
    no literal token ever showed the path, so every literal-token guard was blind (red-team
    P0-3/P0-4). Evaluated across the WHOLE command because the producer and the consumer are
    usually in different segments of a pipeline."""
    producer = None
    risky_root = False
    consumer = False
    for seg in segs:
        try:
            raw = shlex.split(seg.strip())
        except ValueError:
            continue
        if not raw:
            continue
        # Consumer detection must look at the RAW argv: `xargs` is in RUNNERS (so the write-shape
        # classifier can see through `xargs cp`), and stripping it here hid the consumer entirely,
        # letting `find ~ ... | xargs cat` through.
        raw_base = cmd_base(raw[0], {"xargs", "tar"})
        a = strip_runners(raw)
        if not a:
            a = raw
        base = cmd_base(a[0], _RUNTIME_PATH_CMDS)
        if raw_base in ("xargs",) or (raw_base == "tar" and
                                      any(f in raw for f in ("-T", "--files-from"))):
            consumer = True
        if base in _RUNTIME_PATH_CMDS:
            producer = base
            for root in _find_roots(a):
                pat = _abs_pattern(root, None, varmap)
                home = os.path.realpath(os.path.expanduser("~"))
                anchor = str(lib.ANCHOR.resolve())
                # UNRECOVERABLE only: the trust anchor and the sf auth store. A root that merely
                # covers the repo is not risky — the repo is in git. Blocking `find . -delete`
                # there would trade a recoverable file for a tool that feels hostile to use.
                reaches_anchor = (anchor.startswith(pat.rstrip("/") + os.sep)
                                  or anchor_ref(root, None, varmap)
                                  or sf_auth_ref(root, varmap))
                if root in ("~", "/", "$HOME") or root.startswith(("~/", "$HOME")) \
                        or pat in (home, "/") or reaches_anchor:
                    risky_root = True
            if any(f in a for f in _RUNTIME_ACTION_FLAGS):
                consumer = True
    if producer and risky_root and consumer:
        return producer, ("its search root can reach the trust anchor, the sf auth store or the "
                          "gate files, and the results are acted on")
    return None, None


# ---- deferral to the exec-time shim -------------------------------------------------------
#
# THE PROBLEM THIS SOLVES, measured rather than asserted. Replaying six months of real client
# commands through this classifier left 706 denials. Split by cause, 686 of them — 97.2% — are
# some form of "I cannot read this string": substitution, indirect argv0, an interpreter, an
# unbalanced quote. Exactly 20 hit an actual policy boundary.
#
# So analyze_bash answers two different questions and reports both the same way: is this
# operation AUTHORIZED, and can I statically RESOLVE this text. Failing closed on the second is
# correct for a PreToolUse hook, which sees a command string before bash has touched it and
# genuinely cannot know what `$(cat org.txt)` will become. What is not correct is that the user
# cannot tell the two apart — it reads as "Torque says no", and it lands hardest on whoever
# composes shell most fluently, which is the person this tool is for.
#
# The exec-time shim answers the second question exactly. It runs on the argv the kernel is
# about to execute, and bash has finished every expansion by then: `--target-org $(cat org.txt)`
# arrives as `--target-org acme-prod`. There is no text left to reason about. So when the shim
# is verifiably in front of `sf`, this layer does not have to guess — it defers, and the same
# authorization runs downstream on resolved argv instead of a reconstruction.
#
# That is strictly MORE accurate than denying, because the shim sees truth and this sees a guess.
#
# WHAT IS NOT DEFERRABLE, and why each one stays a hard deny:
#   wrapper-sf   `sudo` resets PATH via secure_path, so the shim may not be in front of sf at all.
#   flags-dir    --flags-dir is read by sf ITSELF, not expanded by bash, so the shim cannot see
#                the injected target either. Deferring would hand it to a layer just as blind.
#   runtime-path, stages-local, anchor-ref, auth-ref, protected-write, opaque-writer
#                these are about FILES, not about invoking sf. The shim never sees them.
# The deferrable set is only the cases where the eventual `sf` resolves through PATH in a child
# of this shell, which is precisely where the shim is standing.
DEFERRABLE_TO_SHIM = frozenset({"substitution", "indirect-argv0", "interp-sf",
                                "indirect-sf", "unparseable"})

_SHIM_STATE = {}


def shim_enforcing() -> bool:
    """Is the exec-time shim actually in front of `sf` for commands this session runs?

    Installed is not enough — a shim sitting in the anchor with nothing pointing at it enforces
    nothing. Both halves are required: the operator-controlled copy exists and is executable in
    the anchor, AND resolving `sf` on PATH lands on it.

    Deliberately NOT an environment variable or a config flag. The agent composes its own Bash
    and therefore its own environment; any switch it can set to make this layer quieter is a
    switch it can set to make this layer quieter. What it cannot do is write an executable into
    a 0700 directory outside the workspace that its Bash, Edit, Write and Read cannot reach.
    """
    path = os.environ.get("PATH", "")
    anchor = Path(os.environ.get("TORQUE_ANCHOR", Path.home() / ".torque"))
    key = (path, str(anchor))
    if key in _SHIM_STATE:
        return _SHIM_STATE[key]
    ok = False
    try:
        shim = (anchor / "shim" / "sf").resolve()
        resolved = shutil.which("sf")
        ok = (shim.is_file() and os.access(shim, os.X_OK)
              and resolved is not None and Path(resolved).resolve() == shim)
    except Exception:                                  # noqa: BLE001 — fail CLOSED (no deferral)
        ok = False
    _SHIM_STATE[key] = ok
    return ok


def _deny_or_defer(reason: str, code: str):
    """A static-shape refusal — handed to the shim when the shim is verifiably there.

    Fails closed in both directions that matter: an unknown code is never deferrable, and if
    shim_enforcing() cannot decide it says no. The caller still gets a `deny` key, so any code
    path that has not learned about deferral keeps denying exactly as it did.

    WHEN IT DENIES, IT NAMES THE FIX. This is the difference between a gate and an obstacle. A
    refusal reading "not statically authorizable" tells the user what the parser could not do and
    nothing about what they can do, and on the real corpus that message was 686 of 706 denials —
    every one of which would have gone through with the shim installed. A user who has hit it
    twice concludes the tool is broken, and they are not wrong to, because nothing in the message
    distinguishes "this operation is unsafe" from "this layer cannot read shell".
    """
    if code in DEFERRABLE_TO_SHIM:
        if shim_enforcing():
            return {"deny": None, "writes": [], "mutations": [],
                    "defer": (f"{reason} — deferred to the exec-time shim, which will authorize "
                              f"it on resolved argv", code)}
        return {"deny": (
            f"{reason}\n"
            f"  This is a PARSING limit, not a policy one: the operation was never judged "
            f"unsafe, it could not be read. The exec-time shim resolves it exactly — it sees "
            f"argv after bash has finished every expansion — and with it installed this would "
            f"be authorized normally rather than refused.\n"
            f"    python3 bin/torque install-gates --shim\n"
            f"  Then put it on PATH ahead of the real CLI. It gates only when it cannot see an "
            f"operator at a login terminal, so your own `sf` is unaffected.", code)}
    return {"deny": (reason, code)}


def analyze_bash(cmd: str):
    cmd = strip_continuations(cmd)
    segs, ok = split_segments(cmd)
    if not ok:
        return _deny_or_defer("unparseable command (unbalanced quotes)", "unparseable")
    if grouping_or_subst(cmd) and (SF_SUSPICIOUS.search(cmd) or SF_WORD.search(cmd)):
        return _deny_or_defer("command grouping/substitution around a Salesforce operation — "
                              "not statically authorizable", "substitution")
    varmap = _command_vars(cmd)                       # resolve inline `VAR=...` for path guards (TQ-F1)
    ws = check_write_shapes(segs, varmap)
    if ws:
        return {"deny": ws}
    if re.search(r"(?:^|[\s;&|])(xargs|parallel)\b", cmd) and (SF_WORD.search(cmd) or SF_SUSPICIOUS.search(cmd)):
        return _deny_or_defer("sf routed through xargs/parallel — target not authorizable",
                              "indirect-sf")
    writes, mutations = [], []
    for seg in segs:
        seg = seg.strip()
        if not seg:
            continue
        try:
            argv = shlex.split(seg)
        except ValueError:
            if SF_SUSPICIOUS.search(seg) or SF_WORD.search(seg):
                return _deny_or_defer("unparseable segment carrying a Salesforce operation",
                                      "unparseable")
            continue
        if not argv:
            continue
        if stages_local(argv):
            return {"deny": ("that would put a `local/` path into git — it holds per-org "
                             "findings, session logs with record values, and the audit log. "
                             "It is gitignored, and -f overrides that in one flag.",
                             "stages-local")}
        for tok in argv:
            if anchor_ref(tok, varmap=varmap):
                return {"deny": ("reference to the trust anchor (~/.torque) — secret and tokens "
                                 "are operator-only", "anchor-ref")}
            if sf_auth_ref(tok, varmap):
                return {"deny": ("reference to the sf auth store (~/.sfdx, ~/.sf) — it holds live "
                                 "access tokens", "auth-ref")}
        a, assign_vals = _strip_assignments(argv)
        for v in assign_vals:
            if SF_SUSPICIOUS.search(v) or cmd_base(v, SF_BINS) in SF_BINS:
                return _deny_or_defer("Salesforce operation hidden in a shell assignment value",
                                      "indirect-sf")
        if not a:
            continue
        base0 = cmd_base(a[0], SF_BINS)
        if base0 in SF_BINS:
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
            return _deny_or_defer("indirect command invocation ($VAR/$()/backtick) cannot be "
                                  "authorized — call `sf` literally", "indirect-argv0")
        if base0 in INTERPRETERS and (SF_WORD.search(seg) or SF_SUSPICIOUS.search(seg)
                                      or "`" in seg):
            # EXCEPT the harness itself. `--target-org` matches SF_SUSPICIOUS, so this rule was
            # denying `python3 harness/validate.py --target-org <org>` — the exact command the
            # README and the guide tell every user to run to reproduce the validation. A gate that
            # blocks the product's own headline instruction is a gate people switch off.
            #
            # Safe to exempt because it is not a trust-the-string decision: validate.py is in
            # PROTECTED_BASENAMES, so the agent's Edit/Write on it is already denied, and the path
            # must resolve inside TORQUE_HOME. The agent cannot point this at anything it controls.
            if not _is_own_harness(a):
                return _deny_or_defer(f"Salesforce operation via interpreter/here-string "
                                      f"({base0}) — not authorizable", "interp-sf")
        if wrapped_sf(a):
            return {"deny": ("Salesforce operation under a wrapper/runner — call `sf` directly",
                             "wrapper-sf")}
        # else: a non-sf command with sf only as data (grep sf, echo 'sf ...') → allowed
    _rt, _why = runtime_path_risk(segs, varmap)
    if _rt:
        return {"deny": (f"`{_rt}` builds its target set at run time and {_why} — "
                          f"scope the search, or act on an explicit path", "runtime-path")}

    # A write-shape command combined with command substitution: the substitution fragments the
    # token before check_write_shapes can see it, so the destination is unknowable at parse time.
    # Unknowable destination + a writer = deny (red-team P0-5, demonstrated overwriting a file).
    if ("$(" in cmd or "`" in cmd):
        for _seg in split_segments(strip_continuations(cmd))[0] or []:
            try:
                _a = shlex.split(_seg)
            except ValueError:
                continue
            _a = strip_runners(_a)
            if _a and cmd_base(_a[0], WRITE_SHAPE_CMDS | PERM_CMDS) in (WRITE_SHAPE_CMDS | PERM_CMDS):
                return {"deny": ("a write whose destination is built by command substitution "
                                 "cannot be checked — use an explicit path", "subst-write")}
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


def _norm_key(k):
    return re.sub(r"[_\-]", "", str(k)).lower()


_MCP_ORG_KEYS_NORM = None


def mcp_target(tinput):
    """The org named in an MCP tool's arguments, whatever case or separator names it.

    MCP_ORG_KEYS listed camelCase, kebab-case and lowercase spellings and no snake_case ones —
    no `target_org`, no `org_id`, no `instance_url`. Python MCP servers use snake_case as a
    matter of course, so this was not an exotic spelling; it was the majority convention for
    half the ecosystem. Normalising the key closes camel, kebab, snake and upper together
    instead of adding three entries and waiting for the fourth.
    """
    global _MCP_ORG_KEYS_NORM
    if _MCP_ORG_KEYS_NORM is None:
        _MCP_ORG_KEYS_NORM = {_norm_key(k) for k in MCP_ORG_KEYS}

    # Searched only the TOP level, and an MCP tool's arguments are whatever shape its schema
    # says. A server nesting them under `args` or `params` — ordinary in the ecosystem — had
    # every write denied for "no target" with nothing the operator could do about it, since the
    # shape is the server's to choose, not theirs. Searching the structure identifies the org
    # instead, which is better on both counts: the write gets classified rather than refused,
    # and an org that was previously invisible to the gate is now named. Depth-bounded, because
    # this runs on the blocking path and the input is caller-controlled.
    def walk(node, depth=0):
        if depth > 6:
            return None
        if isinstance(node, dict):
            for k, v in node.items():
                if v and not isinstance(v, (dict, list)) and _norm_key(k) in _MCP_ORG_KEYS_NORM:
                    return v
            for v in node.values():
                found = walk(v, depth + 1)
                if found:
                    return found
        elif isinstance(node, list):
            for v in node[:32]:
                found = walk(v, depth + 1)
                if found:
                    return found
        return None
    return walk(tinput)


def _name_comps(name):
    # split on _ - AND camelCase AND ACRONYM boundaries so `bulkDeleteRecords` and `HTTPDelete`
    # both yield {…,delete,…} (audit TQ-F4 / F5)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)   # HTTPDelete → HTTP_Delete
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)         # bulkDelete → bulk_Delete
    return set(re.split(r"[_-]", s.lower()))


def _mcp_destructive(name, tinput):
    # component-matched (so `get_settings` doesn't match `set`, audit T12-03); name is RAW so
    # camelCase survives into _name_comps (audit TQ-F4)
    comps = _name_comps(name)
    nl = name.lower()
    no_id = not (tinput.get("recordId") or tinput.get("id") or tinput.get("record-id"))
    if "anonymous" in comps or ("apex" in comps and comps & {"run", "execute", "exec"}):
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
    # Presence, not truthiness: an empty string or list would be falsy, and the question is
    # whether a destructive manifest was supplied at all.
    if "deploy" in comps and any(k in tinput for k in
                                 ("preDestructiveChanges", "postDestructiveChanges",
                                  "pre-destructive-changes", "post-destructive-changes")):
        return ("destructive-metadata", "", str(tinput))
    return None


def is_mcp_tool(tool: str) -> bool:
    """Any tool name that names a server, in either host convention.

    Lives here because mcp_analyze() is here, and because the two gates each carried a private
    copy of it — identical logic, different docstrings, which is how _shield_tokens and
    _shield_text came to disagree about casing while both looked maintained. One boundary, one
    implementation.
    """
    return "__" in (tool or "") and tool not in ("Bash", "Read", "Edit", "Write", "MultiEdit")


def mcp_analyze(tool, tinput):
    """{'read':True} | {'write':target, 'destructive':(op,digest,body)|None}. TRUE default-deny:
    an org-touching tool that is not clearly a read is a write (audit R11-04/R11-05)."""
    # A non-dict tool_input (null, a list, a bare string) reached `.get` and raised, so the gate
    # answered "gate crashed, failing closed: AttributeError" — a correct outcome delivered as an
    # alarming non-answer. Same shape as the count path that lost its reason while building it.
    # Classification then proceeds on the tool NAME alone, which still yields write-with-no-target
    # and denies, with a message that says what happened.
    if not isinstance(tinput, dict):
        tinput = {}
    parts = tool.split("__")
    # `mcp__<server>__<tool>` puts the server second; a host that names tools `<server>__<tool>`
    # puts it FIRST, and this read "" for that shape — so is_sf went false and a write tool was
    # classified as a free read. Claude Code uses the three-part form, which is why it never
    # showed up here (release panel round 2, antigravity/gemini-3.1-pro).
    server = (parts[1] if len(parts) > 2 else parts[0] if len(parts) == 2 else "").lower()
    name = parts[-1]                                   # RAW (preserve camelCase for _name_comps)
    nl = name.lower()
    target = mcp_target(tinput)
    comps = _name_comps(name)
    dest = _mcp_destructive(name, tinput)
    # a Salesforce-namespaced server (or an org param) is what we gate; a non-SF MCP server's
    # write tool with no org param (e.g. GitHub `createIssue`) is out of scope (audit TQ-F3).
    # `\b` is a WORD boundary and `_` is a word character, so `sfdx\b` never matched
    # `sfdx_prod` — an underscored Salesforce MCP server was not recognised as Salesforce
    # at all, and its write tools classified as reads and were allowed. Underscores are
    # ordinary in MCP server names, so this was reachable with no evasion (found by the
    # external panel, antigravity/gemini-3.1-pro).
    # `sfdc` is the commonest abbreviation for Salesforce and was absent, and the match was
    # anchored at the start, so `sfdc-tools`, `simple-salesforce` and `steampipe-sfdc` were read
    # as non-Salesforce servers and their write tools classified as free reads. Server names are
    # operator-chosen, so one containing salesforce/sfdc/sfdx is that and nothing else; only the
    # two-letter `sf` stays anchored, being too short to match safely anywhere in a name.
    is_sf = (bool(re.search(r"(salesforce|sfdc|sfdx)", server))
             or bool(re.match(r"sf([_\-]|$)", server)) or target is not None)
    # ANY write verb anywhere in the name makes it a write (get_or_create_record, audit TQ-008)
    writeish = bool(comps & MCP_WRITE_LEADS) or "apex" in nl or "anonymous" in nl
    readish = bool(comps & MCP_READ_LEADS) or nl.startswith(("soql", "tooling", "schema"))
    # The scope rule below — non-Salesforce server AND no org parameter ⇒ out of scope — already
    # existed for ordinary writes, and this branch returned before reaching it. So Torque, a
    # Salesforce tool, demanded a Salesforce approval token to update a Notion page, delete a
    # Slack message or remove a calendar event. That is not a security posture, it is the reason
    # people switch hooks off in week two, and the guide says as much in its own words. Anything
    # carrying an org parameter is is_sf regardless of server, so nothing Salesforce-shaped is
    # released here.
    if dest and is_sf:
        return {"write": target, "destructive": dest}  # destructive shape ⇒ token (org checked by prod gate)
    if writeish:
        return {"write": target, "destructive": None} if is_sf else {"read": True}
    if readish:
        return {"read": True}
    if target is not None:
        return {"write": target, "destructive": None}  # org-touching, unknown verb ⇒ default-deny
    return {"read": True}                               # no org, not writeish ⇒ local tool
