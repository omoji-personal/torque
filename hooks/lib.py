"""Torque hook library — shared classification, allowlist, approval, audit, redaction.

Design invariants (converged plan):
  - Writes authorized by IDENTITY, not inference: membership in writable-orgs.json AND a
    live non-production verdict at write time. Production is ineligible by construction.
  - Classification cache keyed by USERNAME; invalidated when the local auth file's orgId
    for that username mismatches the cached one (recreated orgs can't inherit a verdict).
  - Approval tokens are minted ONLY by an operator-present issuer (bin/torque-approve,
    TTY + process-ancestry bound). A hook can consult a token, never create one.
  - Everything under TORQUE_HOME is fail-closed. Paths resolve from TORQUE_HOME, never CWD.
"""
from __future__ import annotations
import hashlib, json, os, re, subprocess, sys, time
from pathlib import Path

TORQUE_HOME = Path(os.environ.get("TORQUE_HOME", Path(__file__).resolve().parent.parent))
LOCAL = TORQUE_HOME / "local"
# Trust anchor lives OUTSIDE the agent-writable workspace (mode 0700). Tokens, the HMAC
# signing secret, and approved-Apex copies live here so an agent editing the repo cannot
# forge them. Protected further by the Bash write/read denial in prod_write_gate.
ANCHOR = Path(os.environ.get("TORQUE_ANCHOR", Path.home() / ".torque"))
SECRET = ANCHOR / "secret"
APPROVED = ANCHOR / "approved"
PROD_SESSIONS = ANCHOR / "prod-sessions"           # signed, time-boxed prod-write windows
ALLOWLIST = LOCAL / "writable-orgs.json"
CACHE = LOCAL / ".classify-cache.json"
ORGS = LOCAL / "orgs"                       # per-org knowledge, keyed by 18-char orgId
ALIAS_INDEX = LOCAL / ".alias-index.json"   # alias → orgId, for NOTES only, never for authz
TOKENS = ANCHOR / "tokens"
# Overridable for the same reason TORQUE_HOME and TORQUE_ANCHOR are: a test must be able to
# assert what lands in the trail without writing to the operator's real one. Anyone able to set
# this variable already controls the process the hook runs in, so it widens nothing.
AUDIT = Path(os.environ.get("TORQUE_AUDIT_LOG", LOCAL / "audit.log"))
PROTECTED = TORQUE_HOME / "harness" / "checks" / "protected-objects"

ELIGIBLE = {"sandbox", "developer", "scratch"}   # NOT production, NOT unverifiable

# WALL-CLOCK BUDGET for the whole gate, not per callout. Bounding each `sf` call individually
# was not enough: classify_live makes TWO sequential calls, so a per-call 45s cap allowed 90s —
# past Claude Code's hook timeout, which kills the hook at a non-2 status ⇒ fail-OPEN, exactly
# the hole T10-07 closed. The budget is set once when the gate starts; every callout is clamped
# to what remains, and an exhausted budget is a failure (⇒ "production" ⇒ deny), never a pass.
GATE_BUDGET_S = float(os.environ.get("TORQUE_GATE_BUDGET", "40"))
_DEADLINE = None

def start_budget(seconds: float = None):
    global _DEADLINE
    _DEADLINE = time.time() + (GATE_BUDGET_S if seconds is None else seconds)

def _budget_left():
    return None if _DEADLINE is None else _DEADLINE - time.time()

class _Failed:
    returncode = 124
    stdout = ""
    stderr = "sf callout timed out, failed, or the gate time budget was exhausted"

def _sf(*args, timeout=45):
    left = _budget_left()
    if left is not None:
        if left <= 0.5:
            return _Failed()                       # budget gone ⇒ fail safe, do not call out
        timeout = min(timeout, left)
    try:
        return subprocess.run(["sf", *args], capture_output=True, text=True, timeout=timeout)
    except Exception:
        return _Failed()

def approve_cmd(*args) -> str:
    """The remediation command a human can actually paste.

    `torque` is NOT on PATH — nothing installs it there — so printing a bare `torque approve ...`
    told the operator to run a command that does not resolve. Every gate, and the installer, now
    emit this one resolved form, so the string we print is the string that runs regardless of the
    caller's CWD or PATH.
    """
    return " ".join(["python3", str(TORQUE_HOME / "bin" / "torque"), "approve", *args])


def audit(decision: str, detail: str):
    # Never raises: a gate that can't write its audit line must still be able to DENY.
    # (Audit-write failure was an exploit path — chmod 555 local ⇒ crash ⇒ fail-open. K-HM1.)
    try:
        LOCAL.mkdir(exist_ok=True)
        # redact() existed but was never called — the audit log recorded raw command detail while
        # the privacy rule claimed otherwise. Session ids, tokens and org ids are stripped here.
        line = json.dumps({"t": int(time.time()), "decision": decision, "detail": redact(detail)[:400]})
        with open(AUDIT, "a") as f:
            f.write(line + "\n")
        os.chmod(AUDIT, 0o600)
    except Exception:
        pass

def redact(text: str) -> str:
    text = re.sub(r"(sid" + r"=)[^&\s\"']+", r"\1REDACTED", text)
    text = re.sub("(" + "access"+"_token|"+"refresh"+"_token)" + r"[\"'=: ]+[^&\s\"']+", r"\1=REDACTED", text)
    text = re.sub("00D"+r"[A-Za-z0-9]{12,15}", "00D_REDACTED", text)
    return text

# ---- allowlist (fail-closed) ---------------------------------------------
def load_allowlist():
    """Returns dict keyed by 18-char orgId, or None (⇒ deny) on any problem."""
    if not ALLOWLIST.exists():
        return None
    try:
        data = json.loads(ALLOWLIST.read_text())
        out = {}
        for e in data.get("orgs", []):
            oid = norm_id(e["orgId"])
            if e.get("verdict") not in ELIGIBLE:      # never allow production/unverifiable
                continue
            out[oid] = e
        return out
    except Exception:
        return None                                   # malformed ⇒ deny

def norm_id(oid: str) -> str:
    """Normalize a Salesforce Id to 18 chars (15↔18)."""
    if not oid: return oid
    oid = oid.strip()
    if len(oid) == 18: return oid
    if len(oid) != 15: return oid
    suffix = ""
    for chunk in (oid[0:5], oid[5:10], oid[10:15]):
        bits = "".join("1" if c.isupper() else "0" for c in reversed(chunk))
        suffix += "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"[int(bits, 2)]
    return oid + suffix

# ---- classification (username-keyed cache, orgId-invalidated) -------------
def _auth_orgid(username: str) -> str | None:
    r = _sf("org", "display", "--target-org", username, "--json")
    if r.returncode != 0: return None
    try: return norm_id(json.loads(r.stdout)["result"]["id"])
    except Exception: return None

def _is_scratch_org(orgid: str, username: str) -> bool:
    """True only if THIS org (by orgId, then username/alias) is a scratch org per the local
    dev-hub's org list. Scoped to the specific org — never a substring test against the whole
    list output, which would misclassify a trial PRODUCTION org as scratch whenever any other
    scratch org happens to be authenticated (audit finding K-6)."""
    ol = _sf("org", "list", "--json")
    if ol.returncode != 0:
        return False
    try:
        res = json.loads(ol.stdout).get("result", {})
    except Exception:
        return False
    for e in res.get("scratchOrgs", []):
        # Match by orgId ONLY. username/alias equality was forgeable via `sf alias set`
        # (audit R11-07), which would let a trial PRODUCTION org classify as scratch.
        if orgid and norm_id(e.get("orgId", "")) == orgid:
            return True
    return False

def _verdict_from_org_record(rec, orgid, username):
    # Strict type checks: a malformed/adversarial `"IsSandbox":"false"` is a truthy STRING in
    # Python and would misclassify production as sandbox (audit codex #10). Demand a real bool.
    if rec.get("IsSandbox") is True:
        return "sandbox"
    if isinstance(rec.get("OrganizationType"), str) and rec.get("OrganizationType") == "Developer Edition":
        return "developer"
    if rec.get("TrialExpirationDate"):
        return "scratch" if _is_scratch_org(orgid, username) else "production"
    return "production"

def classify_live(target: str):
    """Security-critical classification: ALWAYS a fresh live query, never the cache.
    The cache is agent-writable and must not be trusted for an authorization decision."""
    disp = _sf("org", "display", "--target-org", target, "--json")
    if disp.returncode != 0:
        return "production", None, None
    d = json.loads(disp.stdout)["result"]
    username, orgid = d.get("username"), norm_id(d.get("id"))
    q = "SELECT IsSandbox, OrganizationType, TrialExpirationDate FROM Organization"
    r = _sf("data", "query", "--target-org", target, "--json", "--query", q)
    if r.returncode != 0:
        return "production", orgid, username
    rec = json.loads(r.stdout)["result"]["records"][0]
    return _verdict_from_org_record(rec, orgid, username), orgid, username

def classify(target: str):
    """(verdict, orgId, username). verdict ∈ production|sandbox|developer|scratch.
    Unverifiable ⇒ production (fail-safe)."""
    disp = _sf("org", "display", "--target-org", target, "--json")
    if disp.returncode != 0:
        return "production", None, None               # unverifiable ⇒ production
    d = json.loads(disp.stdout)["result"]
    username, orgid = d.get("username"), norm_id(d.get("id"))
    # cache check keyed by username, invalidated on orgId drift
    cache = {}
    if CACHE.exists():
        try: cache = json.loads(CACHE.read_text())
        except Exception: cache = {}
    _remember_alias(target, orgid)
    hit = cache.get(username)
    if hit and hit.get("orgId") == orgid:
        return hit["verdict"], orgid, username
    q = "SELECT IsSandbox, OrganizationType, TrialExpirationDate FROM Organization"
    r = _sf("data", "query", "--target-org", target, "--json", "--query", q)
    if r.returncode != 0:
        return "production", orgid, username
    rec = json.loads(r.stdout)["result"]["records"][0]
    verdict = _verdict_from_org_record(rec, orgid, username)
    cache[username] = {"orgId": orgid, "verdict": verdict, "t": int(time.time())}
    try:
        LOCAL.mkdir(exist_ok=True); CACHE.write_text(json.dumps(cache))
    except OSError:
        pass                                          # cache is a convenience; never fatal
    return verdict, orgid, username

# ---- write authorization (identity, verified at write time) --------------
def authorize_write(target: str, op_hint: str = "write"):
    """(ok, reason). Non-production: allowlist membership + live non-prod verdict. Production:
    DENIED BY DEFAULT; allowed only through a deliberate operator-present override — a valid
    time-boxed session grant, or a single-use prod token minted by bin/torque-approve. The
    agent can request either; it cannot mint one."""
    verdict, orgid, username = classify_live(target)   # security path: never trust the cache
    if verdict == "production":
        if _prod_session_valid(orgid):
            audit("PROD-WRITE", f"session-authorized {op_hint} on {orgid} ({target})")
            return True, f"{target}: PRODUCTION write authorized via active operator session"
        if consume_token(orgid, "prod-write"):
            audit("PROD-WRITE", f"token-authorized {op_hint} on {orgid} ({target})")
            return True, f"{target}: PRODUCTION write authorized via single-use operator token"
        return False, (f"{target} is PRODUCTION — denied by default. Operator override: "
                       f"`{approve_cmd(target, '<op>', '--prod')}` (one operation) or "
                       f"`{approve_cmd(target, '--session', '<minutes>')}` (a window).")
    allow = load_allowlist()
    if allow is None:
        return False, "allowlist absent/unreadable/malformed — fail-closed deny"
    if orgid not in allow:
        return False, f"{target} (org {orgid}) is not on the write allowlist"
    return True, f"{target} authorized: on allowlist, verdict={verdict}"

def _prod_session_valid(orgid: str) -> bool:
    """A signed, unexpired, orgId-bound production-write window. Not single-use (it is a
    window); revoked by deleting the grant. Forging needs the anchor secret (operator-only)."""
    if not orgid:
        return False
    p = PROD_SESSIONS / f"{orgid}.grant"
    if not p.exists():
        return False
    try:
        g = json.loads(p.read_text())
    except Exception:
        return False
    sig = g.pop("sig", None)
    if not sig or not _hmac.compare_digest(sig, sign(g)):
        return False
    return g.get("orgId") == orgid and g.get("exp", 0) > time.time()

# ---- approval tokens (consulted here; MINTED only by bin/torque-approve) --
import hmac as _hmac

def _secret() -> bytes | None:
    try: return SECRET.read_bytes()
    except Exception: return None

def sign(payload: dict) -> str:
    sec = _secret()
    if sec is None: return ""
    body = json.dumps(payload, sort_keys=True).encode()
    return _hmac.new(sec, body, hashlib.sha256).hexdigest()

def token_path(orgid: str, op_class: str, digest: str = "") -> Path:
    key = hashlib.sha256(f"{orgid}|{op_class}|{digest}".encode()).hexdigest()[:24]
    return TOKENS / f"{key}.token"

def consume_token(orgid: str, op_class: str, digest: str = "") -> bool:
    """Verify a single-use, HMAC-SIGNED token. Consumed via an ATOMIC rename-claim BEFORE
    reading (audit R11-09): two concurrent gate processes cannot both read the same token —
    only the winner of os.rename proceeds; the loser gets ENOENT. Forging requires the anchor
    secret (operator-only); same-uid disclosure of the secret is the Layer-0 boundary."""
    p = token_path(orgid, op_class, digest)
    claim = Path(str(p) + f".claim.{os.getpid()}")
    try:
        os.rename(p, claim)                           # atomic single-use claim
    except OSError:
        return False
    try:
        payload = json.loads(claim.read_text())
        claim.unlink()
        sig = payload.pop("sig", None)
        if not sig or not _hmac.compare_digest(sig, sign(payload)):
            audit("DENY", f"token signature invalid for {op_class} on {orgid}")
            return False
        return payload.get("orgId") == orgid and payload.get("op") == op_class \
            and payload.get("digest", "") == digest and payload.get("exp", 0) > time.time()
    except Exception:
        try: claim.unlink()
        except OSError: pass
        return False

# ---- protected objects ---------------------------------------------------
def protected_objects() -> set:
    if not PROTECTED.exists(): return set()
    return {l.split("#")[0].strip() for l in PROTECTED.read_text().splitlines()
            if l.split("#")[0].strip()}

# ---- PreToolUse I/O ------------------------------------------------------
class InvalidEvent(Exception):
    """Stdin was empty or not JSON. The gate cannot evaluate what it cannot parse."""


# ── platform knowledge, injected at the moment of the operation ────────────────────────────
_KB_PATH = TORQUE_HOME / "knowledge" / "salesforce-platform.yml"
_KB_CACHE = None


def _remember_alias(alias: str, orgid: str):
    """Record alias → orgId so a note can be selected without a callout.

    NOTES ONLY. Authorization re-derives the org live on every write, and must keep doing so —
    cache_poison_resistant exists to prove a poisoned cache cannot flip a verdict. This index is
    allowed to be stale because the worst a stale entry can do is surface the wrong org's note,
    which is visible to the reader and changes no decision.
    """
    if not alias or not orgid:
        return
    try:
        idx = json.loads(ALIAS_INDEX.read_text()) if ALIAS_INDEX.exists() else {}
    except Exception:
        idx = {}
    if idx.get(alias) == orgid:
        return
    idx[alias] = orgid
    try:
        LOCAL.mkdir(exist_ok=True)
        ALIAS_INDEX.write_text(json.dumps(idx))
        os.chmod(ALIAS_INDEX, 0o600)
    except OSError:
        pass


_TARGET_ORG = re.compile(r"--target-org[= ]+([A-Za-z0-9._@-]+)|(?:^|\s)-o\s+([A-Za-z0-9._@-]+)")


def org_for_command(command: str):
    """The 18-char orgId this command targets, from the index. None when unknown."""
    m = _TARGET_ORG.search(command or "")
    if not m:
        return None
    alias = m.group(1) or m.group(2)
    try:
        return json.loads(ALIAS_INDEX.read_text()).get(alias)
    except Exception:
        return None


def org_notes(command: str, limit: int = 2):
    """Findings recorded against THIS org that match this command.

    Platform knowledge tells you what Salesforce does. This tells you what THIS org does — the
    thing a consultant actually carries between engagements and currently keeps in their head.
    It is keyed by orgId rather than alias on purpose: a sandbox refresh mints a new orgId, so
    the memory correctly empties when the org it described no longer exists.
    """
    orgid = org_for_command(command)
    if not orgid:
        return []
    f = ORGS / f"{orgid}.yml"
    if not f.exists():
        return []
    hits = []
    for e in _parse_org_file(f):
        matched = 0
        for pat in e.get("triggers") or []:
            try:
                if re.search(pat, command, re.I):
                    matched += 1
            except re.error:
                continue
        if matched:
            hits.append((matched, e))
    hits.sort(key=lambda t: -t[0])
    return [e for _, e in hits[:limit]]


def _parse_org_file(f):
    out, cur, key = [], None, None
    try:
        for raw in f.read_text().split("\n"):
            if raw.startswith("- id:"):
                cur = {"id": raw.split(":", 1)[1].strip()}
                out.append(cur); key = None
            elif cur is None or not raw.startswith("  "):
                continue
            elif raw.startswith("  triggers:"):
                body = raw.split("[", 1)[-1].rsplit("]", 1)[0]
                cur["triggers"] = [_yaml_unquote(t) for t in body.split(",") if t.strip()]
            elif not raw.startswith("    ") and ":" in raw:
                k, _, v = raw.strip().partition(":")
                v = v.strip()
                cur[k] = "" if v in (">", "|") else _yaml_unquote(v)
                key = k if v in (">", "|") else None
            elif key and raw.startswith("    "):
                cur[key] = (cur.get(key, "") + " " + raw.strip()).strip()
    except Exception:
        return []
    return out


def _yaml_unquote(v: str) -> str:
    """Strip exactly ONE matching pair of quotes, then unescape.

    This was `v.strip("'\"")`, which strips EVERY leading and trailing quote character — so a
    value legitimately ending in a quote lost it. The catalogue's detect probes are the case
    that exposed it: `"SELECT ... WHERE Field = 'Account.My_Field__c'"` came back missing its
    final apostrophe and every probe failed with MALFORMED_QUERY. The same three-line reader
    had been copied into three files, so all three were wrong the same way.
    """
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        q, v = v[0], v[1:-1]
        v = v.replace("''", "'") if q == "'" else v.replace('\\"', '"')
    return v


def _kb_entries():
    """Catalogue entries with triggers. Parsed without PyYAML so a gate never depends on it."""
    global _KB_CACHE
    if _KB_CACHE is not None:
        return _KB_CACHE
    out, cur, key = [], None, None
    try:
        for raw in _KB_PATH.read_text().split("\n"):
            if raw.startswith("- id:"):
                cur = {"id": raw.split(":", 1)[1].strip()}
                out.append(cur); key = None
            elif cur is None or not raw.startswith("  "):
                continue
            elif raw.startswith("  triggers:"):
                body = raw.split("[", 1)[-1].rsplit("]", 1)[0]
                cur["triggers"] = [_yaml_unquote(t) for t in body.split(",") if t.strip()]
            elif not raw.startswith("    ") and ":" in raw:
                k, _, v = raw.strip().partition(":")
                v = v.strip()
                cur[k] = "" if v in (">", "|") else _yaml_unquote(v)
                key = k if v in (">", "|") else None
            elif key and raw.startswith("    "):
                cur[key] = (cur.get(key, "") + " " + raw.strip()).strip()
    except Exception:
        out = []
    _KB_CACHE = out
    return out


def platform_notes(command: str, limit: int = 2):
    """Catalogue entries relevant to THIS command.

    The catalogue was a file the agent might consult if a model-honoured rule reminded it to.
    That is the weakest possible delivery for knowledge whose entire value is being present at
    one specific moment: the instant before a command runs. The gate is already reading every
    command, already deciding, and already speaking — so it is the natural place for the
    platform to answer back.
    """
    if not command:
        return []
    hits = []
    for e in _kb_entries():
        matched = 0
        for pat in e.get("triggers") or []:
            try:
                if re.search(pat, command, re.I):
                    matched += 1
            except re.error:
                continue
        if matched:
            hits.append((matched, e))
    # Rank by how SPECIFICALLY the entry matches this command, then by how well the claim is
    # evidenced. Confidence-first buried the most useful note: for a bulk update the thing worth
    # saying is what a no-op write costs, and that entry is honestly marked `practitioner`.
    order = {"verified-live": 0, "documented": 1, "practitioner": 2}
    hits.sort(key=lambda t: (-t[0], order.get(t[1].get("confidence", ""), 3)))
    return [e for _, e in hits[:limit]]


def _speak(command: str):
    """Emit platform notes, never at the cost of the verdict."""
    if not command:
        return
    try:
        emit_org_notes(command)          # what THIS org does outranks what the platform does
        emit_platform_notes(command)
    except Exception:
        pass              # knowledge is a courtesy; a broken catalogue must not change an exit code


def emit_org_notes(command: str):
    """Print findings recorded against this specific org."""
    if os.environ.get("TORQUE_NO_NOTES") == "1":
        return
    for e in org_notes(command):
        obs = " ".join((e.get("observed") or "").split())
        rem = " ".join((e.get("remedy") or "").split())
        if len(rem) > 200:
            rem = rem[:197].rsplit(" ", 1)[0] + "…"
        print(f"TORQUE ORG NOTE [{e.get('id')}] {obs}", file=sys.stderr)
        if rem:
            print(f"  → {rem}", file=sys.stderr)


def emit_platform_notes(command: str):
    """Print relevant platform knowledge to stderr, where the transcript will carry it."""
    if os.environ.get("TORQUE_NO_NOTES") == "1":
        return
    for e in platform_notes(command):
        title = e.get("title", "").strip()
        remedy = " ".join((e.get("remedy") or "").split())
        if len(remedy) > 210:
            remedy = remedy[:207].rsplit(" ", 1)[0] + "…"
        print(f"TORQUE PLATFORM NOTE [{e.get('id')}] {title}\n  → {remedy}", file=sys.stderr)


_JUDGED = {"command": ""}


def remember_command(command: str):
    """Record what is being judged, so both exits can speak about it."""
    _JUDGED["command"] = command or ""


def read_event():
    """Parse the hook payload, or raise.

    This used to swallow BOTH failures and return {} — an empty dict has no tool_name and no
    command, so every classifier fell through to allow(). Empty stdin and malformed JSON both
    exited 0. That is a FAIL-OPEN in the one function every gate starts with, in a product
    whose stated posture is that a gate which cannot decide must deny (external panel,
    codex gpt-5.6-sol).
    """
    raw = sys.stdin.read()
    if not raw.strip():
        raise InvalidEvent("empty hook payload")
    try:
        ev = json.loads(raw)
    except Exception as e:
        raise InvalidEvent(f"unparseable hook payload: {type(e).__name__}")
    if not isinstance(ev, dict):
        raise InvalidEvent("hook payload is not an object")
    return ev

_SF_SHAPED = re.compile(r"(^|[|;&(\s])sf\s")


def allow(command: str = ""):
    """Exit 0 — logging the decision, and saying anything the catalogue knows about it.

    Only Salesforce-shaped commands are logged. The alternative readings were both worse: log
    nothing, which is what happened before and made the documented claim of a complete decision
    trail false; or log every Bash call, which buries the twenty decisions that matter under
    ten thousand `ls` and `grep` lines until nobody reads the file.
    """
    cmd = command or _JUDGED["command"]
    if cmd and _SF_SHAPED.search(cmd):
        audit("ALLOW", redact(cmd)[:300])
    _speak(cmd)
    sys.exit(0)

def deny(reason: str, fingerprint: str = "", hook_id: str = ""):
    audit("DENY", f"[{hook_id}:{fingerprint}] {reason}")
    print(f"TORQUE GATE DENY [{hook_id}] {reason}", file=sys.stderr)
    # A deny is the moment the note matters MOST: the operator is about to decide whether to
    # override, and what the operation actually costs is the input to that decision.
    _speak(_JUDGED["command"])
    sys.exit(2)                                       # exit 2 ⇒ Claude Code blocks the tool

def run_gate(main_fn, hook_id: str):
    """Fail-CLOSED wrapper. allow()/deny() raise SystemExit (not Exception) and pass through;
    any OTHER exception denies rather than letting a crash exit 1 (non-blocking = allow).
    Also starts the wall-clock budget so the gate can never outlive the host's hook timeout."""
    start_budget()
    try:
        main_fn()
    except SystemExit:
        raise
    except InvalidEvent as e:
        deny(f"cannot evaluate this tool call ({e}) — failing closed", "invalid-event", hook_id)
    except Exception as e:                            # noqa: BLE001 — fail closed on anything
        deny(f"gate crashed, failing closed: {type(e).__name__}: {e}", "crash", hook_id)


# ---- protected paths: agent Bash must not write these (realpath-resolved) -------------
def protected_write_paths():
    hd = TORQUE_HOME
    # every entry .resolve()'d so a symlink cannot dodge the equality/prefix match (audit R11 RU)
    return [str((hd/"hooks").resolve()), str((hd/"bin").resolve()),
            str((hd/".claude"/"settings.json").resolve()),
            str((hd/"harness"/"checks").resolve()),
            str(ALLOWLIST.resolve()), str(PROTECTED.resolve()),
            str((hd/"harness"/"checks"/"cli-write-surface.json").resolve()),
            str((hd/"harness"/"checks"/"clean-ip.rules").resolve()),
            str(CACHE.resolve()), str(ANCHOR.resolve())]

def is_protected_target(path_str: str) -> bool:
    try: rp = str(Path(path_str).resolve())
    except Exception: rp = path_str
    for prot in protected_write_paths():
        if rp == prot or rp.startswith(prot + "/"):
            return True
    return False
