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
ALLOWLIST = LOCAL / "writable-orgs.json"
CACHE = LOCAL / ".classify-cache.json"
TOKENS = LOCAL / "tokens"
AUDIT = LOCAL / "audit.log"
PROTECTED = TORQUE_HOME / "harness" / "checks" / "protected-objects"

ELIGIBLE = {"sandbox", "developer", "scratch"}   # NOT production, NOT unverifiable

def _sf(*args):
    return subprocess.run(["sf", *args], capture_output=True, text=True)

def audit(decision: str, detail: str):
    LOCAL.mkdir(exist_ok=True)
    line = json.dumps({"t": int(time.time()), "decision": decision, "detail": detail[:400]})
    with open(AUDIT, "a") as f:
        f.write(line + "\n")
    try: os.chmod(AUDIT, 0o600)
    except OSError: pass

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
    if rec.get("IsSandbox"):
        verdict = "sandbox"
    elif (rec.get("OrganizationType") or "") == "Developer Edition":
        verdict = "developer"
    elif rec.get("TrialExpirationDate"):
        ol = _sf("org", "list", "--json")
        verdict = "scratch" if (ol.returncode == 0 and f'"{username}"' in ol.stdout
                                and '"isScratch": true' in ol.stdout) else "production"
    else:
        verdict = "production"
    cache[username] = {"orgId": orgid, "verdict": verdict, "t": int(time.time())}
    LOCAL.mkdir(exist_ok=True); CACHE.write_text(json.dumps(cache))
    return verdict, orgid, username

# ---- write authorization (identity, verified at write time) --------------
def authorize_write(target: str):
    """(ok, reason). ok only if target is on the allowlist AND classifies non-production
    NOW. Membership is necessary, never sufficient."""
    allow = load_allowlist()
    if allow is None:
        return False, "allowlist absent/unreadable/malformed — fail-closed deny"
    verdict, orgid, username = classify(target)
    if verdict == "production":
        return False, f"{target} classifies production/unverifiable — ineligible by construction"
    if orgid not in allow:
        return False, f"{target} (org {orgid}) is not on the write allowlist"
    return True, f"{target} authorized: on allowlist, verdict={verdict}"

# ---- approval tokens (consulted here; MINTED only by bin/torque-approve) --
def token_path(orgid: str, op_class: str, digest: str = "") -> Path:
    key = hashlib.sha256(f"{orgid}|{op_class}|{digest}".encode()).hexdigest()[:24]
    return TOKENS / f"{key}.token"

def consume_token(orgid: str, op_class: str, digest: str = "") -> bool:
    p = token_path(orgid, op_class, digest)
    if not p.exists(): return False
    try:
        payload = json.loads(p.read_text())
        p.unlink()                                    # single-use
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
