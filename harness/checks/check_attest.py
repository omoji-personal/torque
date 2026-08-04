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
