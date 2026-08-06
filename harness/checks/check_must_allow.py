# The must-allow direction, measured against a corpus of REAL commands the repo never sees.
#
# gate_fixtures_r17.json is twenty hand-written cases. It exists because 193 fixtures with 44
# allows among them still missed a defect that denied 71% of six months of genuine client work —
# hand-imagined allow cases are a narrower distribution than real use, and no amount of writing
# more of them fixes that. The corrective is real commands.
#
# Real commands cannot live in this repository. They carry client names, org identifiers, record
# Ids and query text, and the standing constraint is that none of that enters the tree. So the
# corpus stays where the operator keeps it and this check is pointed at it:
#
#     TORQUE_ALLOW_CORPUS=~/somewhere/commands.jsonl python3 harness/validate.py --only must_allow
#
# TWO FORMATS, and the JSON one is the real one. A `.txt` file is one command per line, which
# sounds obvious and cannot represent the data: dumping six months of genuine commands into that
# format kept 35 of 1,193, because real practitioner commands are overwhelmingly multi-line. A
# corpus format that discards 97% of the corpus measures nothing. `.jsonl` — one JSON-encoded
# string per line — round-trips newlines, quotes and every byte that made the command interesting
# in the first place.
#
# Nothing is copied, nothing is summarised into the repo, and the check prints counts and deny
# codes only — never a command.
#
# THE ASSERTION IS AN INVARIANT, NOT A THRESHOLD. With a verified shim, no command may be refused
# for a SHAPE reason — those defer by construction now, so a shape denial means the deferral
# broke. Policy denials are counted and reported but never fail the check: refusing a production
# write is the tool working, and a corpus full of them is a finding about the corpus.
import os as _mos
import sys as _msys
from pathlib import Path as _MAP

_ENV = "TORQUE_ALLOW_CORPUS"


def _load(p):
    """JSONL if the suffix says so, else one command per line. Malformed JSONL is fatal.

    A line that will not decode is dropped by every tolerant parser and silently shrinks the
    corpus, which is the same failure as an empty one wearing a plausible count.
    """
    p = _MAP(p)
    text = p.read_text(errors="ignore")
    if p.suffix in (".jsonl", ".ndjson"):
        out, bad = [], 0
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                v = json.loads(line)
            except Exception:                            # noqa: BLE001
                bad += 1
                continue
            if isinstance(v, str):
                out.append(v)
            elif isinstance(v, dict) and isinstance(v.get("command"), str):
                out.append(v["command"])
            else:
                bad += 1
        return out, bad
    return [ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("#")], 0


@check("must_allow_corpus_has_no_shape_denials", "static")
def _must_allow_corpus_has_no_shape_denials():
    raw = _mos.environ.get(_ENV)
    if not raw:
        # NOT a pass. A check that reports green when it examined nothing is the exact defect
        # this file was written to correct, one level up.
        return Result("must_allow_corpus_has_no_shape_denials", NA,
                      f"no corpus given; set {_ENV} to a file of real commands (one per line) "
                      f"to measure the must-allow direction against actual use rather than "
                      f"against twenty hand-written fixtures")
    path = _MAP(raw).expanduser()
    if not path.is_file():
        return Result("must_allow_corpus_has_no_shape_denials", FAIL,
                      f"{_ENV} points at {path}, which is not a readable file — a corpus that "
                      f"cannot be read is not an empty corpus")
    cmds, unreadable = _load(path)
    if not cmds:
        return Result("must_allow_corpus_has_no_shape_denials", FAIL,
                      f"{path} contained no commands; an empty corpus passes every assertion "
                      f"and proves nothing")
    if unreadable:
        return Result("must_allow_corpus_has_no_shape_denials", FAIL,
                      f"{unreadable} line(s) in {path.name} did not decode; a corpus that "
                      f"silently shrinks reports a rate for a sample nobody chose")

    _msys.path.insert(0, str(ROOT / "hooks"))
    for m in ("shellparse", "lib"):
        _msys.modules.pop(m, None)
    import shellparse
    shellparse._SHIM_STATE.clear()

    shim = shellparse.shim_enforcing()
    shape, policy, allowed, deferred = {}, {}, 0, 0
    for c in cmds:
        try:
            r = shellparse.analyze_bash(c)
        except Exception as e:                          # noqa: BLE001
            shape[f"CLASSIFIER-ERROR:{type(e).__name__}"] = \
                shape.get(f"CLASSIFIER-ERROR:{type(e).__name__}", 0) + 1
            continue
        if r.get("defer"):
            deferred += 1
            continue
        if r.get("deny"):
            code = r["deny"][1]
            bucket = shape if code in shellparse.DEFERRABLE_TO_SHIM else policy
            bucket[code] = bucket.get(code, 0) + 1
            continue
        # A mutation naming no target is refused by the GATE, downstream of analyze_bash — so
        # counting only analyze_bash verdicts reported 18 policy denials where the same corpus
        # measured 22. Both numbers were right about different things, which is how two artifacts
        # in one repo come to disagree about one corpus.
        if any(not shellparse.targets(a)
               for a in (r.get("writes") or []) + (r.get("mutations") or [])):
            policy["no-explicit-target"] = policy.get("no-explicit-target", 0) + 1
            continue
        allowed += 1

    n = len(cmds)
    summary = (f"{n} real commands: {allowed} allowed, {deferred} deferred, "
               f"{sum(policy.values())} policy-denied")
    codes = ", ".join(f"{k}={v}" for k, v in sorted(policy.items())) or "none"

    if not shim:
        # Without a shim every shape class denies on purpose, so the invariant cannot be tested.
        # Saying so beats reporting a green that measured the wrong configuration.
        return Result("must_allow_corpus_has_no_shape_denials", WARN,
                      f"no verified shim on PATH, so shape denials are expected and the "
                      f"invariant is untestable here. {summary}, "
                      f"{sum(shape.values())} shape-denied. policy: {codes}")

    if shape:
        worst = ", ".join(f"{k}={v}" for k, v in sorted(shape.items()))
        return Result("must_allow_corpus_has_no_shape_denials", FAIL,
                      f"a verified shim is on PATH, so no command may be refused for being "
                      f"unreadable — but {sum(shape.values())} of {n} were: {worst}. "
                      f"The deferral is broken, and real work is being refused for a parser "
                      f"limit rather than a policy decision.")
    return Result("must_allow_corpus_has_no_shape_denials", PASS,
                  f"verified shim; zero shape denials. {summary}. policy denials: {codes}")
