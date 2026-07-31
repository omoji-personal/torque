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
TOKENS = ANCHOR / "tokens"
AUDIT = LOCAL / "audit.log"
PROTECTED = TORQUE_HOME / "harness" / "checks" / "protected-objects"

ELIGIBLE = {"sandbox", "developer", "scratch"}   # NOT production, NOT unverifiable

def _sf(*args, timeout=45):
    # A hung `sf` inside a PreToolUse hook would let Claude Code time the hook out with a
    # non-2 status ⇒ fail-OPEN (audit T10-07). Bound every callout and fail safe on timeout.
    try:
        return subprocess.run(["sf", *args], capture_output=True, text=True, timeout=timeout)
    except Exception:
        class _R:
            returncode = 124
            stdout = ""
            stderr = "sf callout timed out or failed"
        return _R()

def audit(decision: str, detail: str):
    # Never raises: a gate that can't write its audit line must still be able to DENY.
    # (Audit-write failure was an exploit path — chmod 555 local ⇒ crash ⇒ fail-open. K-HM1.)
    try:
        LOCAL.mkdir(exist_ok=True)
        line = json.dumps({"t": int(time.time()), "decision": decision, "detail": detail[:400]})
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
        if norm_id(e.get("orgId", "")) == orgid:
            return True
        if username and (e.get("username") == username or e.get("alias") == username):
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
                       f"`torque approve {target} <op> --prod` (one operation) or "
                       f"`torque approve {target} --session <minutes>` (a window).")
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
    """Verify a single-use, HMAC-SIGNED token. Forging requires the anchor secret, which
    lives outside the workspace and is Bash-read-denied — so an agent cannot mint one by
    writing a token file. Same-uid disclosure of the secret is the Layer-0 boundary."""
    p = token_path(orgid, op_class, digest)
    if not p.exists(): return False
    try:
        payload = json.loads(p.read_text())
        p.unlink()                                    # single-use, consumed on read
        sig = payload.pop("sig", None)
        if not sig or not _hmac.compare_digest(sig, sign(payload)):
            audit("DENY", f"token signature invalid for {op_class} on {orgid}")
            return False
        return payload.get("orgId") == orgid and payload.get("op") == op_class \
            and payload.get("digest", "") == digest and payload.get("exp", 0) > time.time()
    except Exception:
        return False

# ---- protected objects ---------------------------------------------------
def protected_objects() -> set:
    if not PROTECTED.exists(): return set()
    return {l.split("#")[0].strip() for l in PROTECTED.read_text().splitlines()
            if l.split("#")[0].strip()}

# ---- PreToolUse I/O ------------------------------------------------------
def read_event():
    try: return json.loads(sys.stdin.read() or "{}")
    except Exception: return {}

def allow():
    sys.exit(0)

def deny(reason: str, fingerprint: str = "", hook_id: str = ""):
    audit("DENY", f"[{hook_id}:{fingerprint}] {reason}")
    print(f"TORQUE GATE DENY [{hook_id}] {reason}", file=sys.stderr)
    sys.exit(2)                                       # exit 2 ⇒ Claude Code blocks the tool

def run_gate(main_fn, hook_id: str):
    """Fail-CLOSED wrapper. allow()/deny() raise SystemExit (not Exception) and pass through;
    any OTHER exception denies rather than letting a crash exit 1 (non-blocking = allow)."""
    try:
        main_fn()
    except SystemExit:
        raise
    except Exception as e:                            # noqa: BLE001 — fail closed on anything
        deny(f"gate crashed, failing closed: {type(e).__name__}: {e}", "crash", hook_id)


# ---- protected paths: agent Bash must not write these (realpath-resolved) -------------
def protected_write_paths():
    hd = TORQUE_HOME
    return [str((hd/"hooks").resolve()), str((hd/"bin").resolve()),
            str((hd/".claude"/"settings.json").resolve()),
            str((hd/"harness"/"checks").resolve()),
            str(ALLOWLIST.resolve()), str(PROTECTED.resolve()),
            str((hd/"harness"/"checks"/"cli-write-surface.json")),
            str((hd/"harness"/"checks"/"clean-ip.rules").resolve()),
            str(CACHE), str(ANCHOR.resolve())]

def is_protected_target(path_str: str) -> bool:
    try: rp = str(Path(path_str).resolve())
    except Exception: rp = path_str
    for prot in protected_write_paths():
        if rp == prot or rp.startswith(prot + "/"):
            return True
    return False
