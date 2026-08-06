# The artifact whose only job is to be trustworthy, held to that.
#
# An attestation exists so a reader who was not there can check what happened. Three files in
# harness/attest/ recorded a tree, a head and a toolchain and no verdict at all — they attested
# that a run occurred, not that it passed — and they sat in the same directory as twenty-one real
# ones with nothing marking the difference. Nobody had written a check, because the directory is
# output rather than source and output does not feel like something that can be wrong.
#
# It can. A verdict-less record among verdict-carrying ones does not read as incomplete, it reads
# as one more attestation, and the count of attestations is the number people quote.
import json as _at_json

_AT_DIR = ROOT / "harness" / "attest"
# Prefixed, like every other module-level name here. The plugin loader execs every check_*.py
# into ONE shared namespace, so a bare _REQUIRED is not this file's _REQUIRED for long:
# check_kb.py defines that name for catalogue entries and loads afterwards, so the first run of
# this check demanded that every attestation carry an id, a symptom and a remedy. Caught in under
# a minute, and only because the check had something real to say the moment it ran.
_AT_REQUIRED = ("schema", "verdict", "profile", "tree", "head")
_AT_VERDICTS = {"PASS", "FAIL", "DEGRADED"}


@check("attestations_carry_a_verdict", "static")
def _attestations_carry_a_verdict():
    """Every record directly under harness/attest/ states what it concluded.

    Scoped to the top level on purpose. `legacy/` holds the pre-schema-2 records, kept as
    evidence of when the format changed rather than deleted — removing the weaker records of
    your own history is how a paper trail comes to look better than the project was. That
    exclusion lives in a README a reader will find, not in a skip list inside this file.
    """
    name = "attestations_carry_a_verdict"
    if not _AT_DIR.exists():
        return Result(name, NA, "no harness/attest directory — nothing has been attested")
    files = sorted(p for p in _AT_DIR.glob("*.json"))
    if not files:
        return Result(name, NA, "harness/attest holds no records")
    bad = []
    verdicts = {}
    for p in files:
        try:
            o = _at_json.loads(p.read_text())
        except Exception as e:                             # noqa: BLE001
            bad.append(f"{p.name} does not parse ({type(e).__name__})")
            continue
        missing = [k for k in _AT_REQUIRED if not o.get(k)]
        if missing:
            bad.append(f"{p.name} states no {', '.join(missing)}")
            continue
        v = o["verdict"]
        if v not in _AT_VERDICTS:
            bad.append(f"{p.name} reports verdict {v!r}, not one of {sorted(_AT_VERDICTS)}")
            continue
        verdicts[v] = verdicts.get(v, 0) + 1
    if bad:
        return Result(name, FAIL, "; ".join(bad))
    legacy = len(list((_AT_DIR / "legacy").glob("*.json"))) if (_AT_DIR / "legacy").is_dir() else 0
    tail = f"; {legacy} pre-schema-2 record(s) held in legacy/ and not counted here" if legacy else ""
    breakdown = ", ".join(f"{n} {v}" for v, n in sorted(verdicts.items()))
    return Result(name, PASS,
                  f"{len(files)} attestation(s), every one carrying a schema, verdict, profile, "
                  f"tree and head ({breakdown}){tail}")


@check("attestation_signature_detects_an_edit", "static")
def _attestation_signature_detects_an_edit():
    """A schema-valid attestation is not an authentic one.

    `attestations_carry_a_verdict` checks SHAPE, so a hand-written record claiming an all-PASS
    release validated — in a directory that was agent-writable until the assurance corpus was
    protected. Attestations are now HMAC-signed with the anchor secret through `lib.sign`.

    What the signature proves, stated honestly rather than implied: attest runs as the operator's
    uid and can read the secret, and so can any same-uid process. This defends against accidental
    corruption and against forgery through the agent's TOOL SURFACE — the surface every other
    control here is scoped to — and becomes a real boundary when the agent's process cannot reach
    the anchor. It is not a claim about a determined same-uid adversary.

    Asserts the mechanism rather than the corpus: records written before signing existed are not
    retroactively suspect, and failing them would be dating a defect to the wrong commit.
    """
    name = "attestation_signature_detects_an_edit"
    import sys as _as
    _as.path.insert(0, str(ROOT / "hooks"))
    for m in ("lib", "shellparse"):
        _as.modules.pop(m, None)
    import lib

    if not lib.SECRET.exists():
        return Result(name, NA,
                      "no anchor secret on this machine, so no signature can be produced or "
                      "verified here — reported rather than passed, because a check that "
                      "examined nothing is not a check that succeeded")

    doc = {"schema": "torque.attestation/2", "verdict": "PASS", "profile": "release",
           "tree": "deadbeef", "head": "cafebabe"}
    doc["sig"] = lib.sign({k: v for k, v in doc.items() if k != "sig"})

    def verifies(d):
        s = d.get("sig")
        return bool(s) and s == lib.sign({k: v for k, v in d.items() if k != "sig"})

    if not verifies(dict(doc)):
        return Result(name, FAIL, "a freshly signed attestation does not verify against lib.sign")

    tampered = dict(doc, verdict="PASS", profile="static")
    if verifies(tampered):
        return Result(name, FAIL,
                      "editing a signed attestation left the signature valid — the signature "
                      "covers less than the document it is attached to")
    forged = dict(doc)
    forged.pop("sig")
    if verifies(forged):
        return Result(name, FAIL, "an unsigned attestation verified")

    src = (ROOT / "bin" / "torque-attest").read_text()
    if "lib.sign(att)" not in src:
        return Result(name, FAIL,
                      "torque-attest no longer signs what it writes, so new attestations are "
                      "unauthenticated regardless of what this check can demonstrate")
    return Result(name, PASS,
                  "attest signs through lib.sign; a signed record verifies, an edited one does "
                  "not, and an unsigned one does not — one HMAC implementation, not a second "
                  "that drifts")
