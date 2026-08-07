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
import re as _vere
import sys as _msys
from pathlib import Path as _MAP

_ENV = "TORQUE_ALLOW_CORPUS"

# THE SHAPE CODES, WRITTEN DOWN HERE RATHER THAN READ FROM THE MODULE UNDER TEST.
#
# Both checks in this file ask: with a verified shim, was anything refused because the parser
# could not READ it? They used to answer by consulting shellparse.DEFERRABLE_TO_SHIM — which is
# the set being tested. Remove a code from it and the denial simply re-buckets as `policy` and
# both checks stay green. Demonstrated 2026-08-06: dropping `substitution` and `unparseable` from
# the deferral set moved four denials from shape to policy and the check PASSED.
#
# That is the same defect this repository found three times today at the check layer — a check
# that inherits its definition from the thing it measures. A literal costs a line of maintenance
# when a genuinely new shape class is added, and buys a check that can fail.
#
# `shape_codes_are_still_the_deferrable_ones` below asserts the two lists agree, so the literal
# cannot silently drift into being wrong instead of merely stale.
_SHAPE_CODES = frozenset({"substitution", "indirect-argv0", "interp-sf",
                          "indirect-sf", "unparseable"})


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


def _ve_harvest():
    """Every command example the INSTALLED Salesforce CLI publishes, harvested at run time.

    A SECOND corpus, deliberately not this file's first one. must_allow_corpus_has_no_shape_denials
    asks about ACTUAL USE — six months of a practitioner's commands, overwhelmingly multi-line,
    full of client data, and therefore unable to live in this repository. It stays NA until an
    operator points it at their own file, and pointing it at vendor documentation instead would
    convert an honest NA into a PASS measured against a weaker distribution. Documented usage is
    pristine and single-line; that narrowness is the very thing the other check exists to escape.

    What this one asks is narrower and still worth standing: does the parser refuse commands
    SALESFORCE ITSELF PUBLISHES? Nobody wrote them to pass or fail a gate, and a shape denial
    against one means the tool rejects its own vendor's documented usage.

    HARVESTED, NOT COMMITTED. A checked-in corpus goes stale the moment a plugin updates, and
    would be a 50KB blob nobody re-derives. This reads the oclif manifests on disk, so it measures
    the CLI actually installed. Two roots: the core install, and the plugins the user added —
    reading only the first collected 457 of 763, with the 107 missing ids concentrated in `agent`,
    `template` and `devops`, exactly the plugins somebody installs on purpose.

    Returns (commands, manifest_count). No CLI ⇒ empty, and the caller reports NA rather than
    passing an assertion it never made.
    """
    import glob as _veg
    roots = ("/usr/local/lib/sf", _mos.path.expanduser("~/.local/share/sf"))
    mans = []
    for r in roots:
        mans.extend(_veg.glob(r + "/**/oclif.manifest.json", recursive=True))
    seen, out = set(), []
    for man in mans:
        try:
            with open(man) as fh:
                doc = json.load(fh)
        except Exception:                                    # noqa: BLE001
            continue
        for cid, meta in (doc.get("commands") or {}).items():
            for ex in (meta.get("examples") or []):
                body = ex.get("command") if isinstance(ex, dict) else ex
                if not isinstance(body, str):
                    continue
                for line in body.splitlines():
                    line = _vere.sub(r"<%=\s*config\.bin\s*%>", "sf", line)
                    line = _vere.sub(r"<%=\s*command\.id\s*%>", cid.replace(":", " "), line)
                    line = _vere.sub(r"<%=.*?%>", "", line).strip()
                    if line.startswith("sf ") and line not in seen:
                        seen.add(line)
                        out.append(line)
    return out, len(mans)


@check("shape_codes_are_still_the_deferrable_ones", "static")
def _shape_codes_are_still_the_deferrable_ones():
    """The literal above must still name exactly the classes shellparse defers.

    The literal exists so the two checks in this file can FAIL when the deferral set changes —
    reading the set under test made that impossible. But a hardcoded copy rots, and a stale copy
    is a check measuring a world that no longer exists. This is the join: buckets come from the
    literal, and the literal is compared to the real thing here, once, loudly.

    Both directions matter. A class ADDED to shellparse and not here means the new class is
    silently counted as policy and never fails the invariant. A class REMOVED from shellparse and
    left here means the checks assert a deferral that no longer happens.
    """
    name = "shape_codes_are_still_the_deferrable_ones"
    _msys.path.insert(0, str(ROOT / "hooks"))
    for m in ("shellparse", "lib"):
        _msys.modules.pop(m, None)
    import shellparse

    real = set(shellparse.DEFERRABLE_TO_SHIM)
    mine = set(_SHAPE_CODES)
    if real == mine:
        return Result(name, PASS,
                      f"{len(mine)} shape class(es) named identically here and in shellparse "
                      f"({', '.join(sorted(mine))}) — the buckets can fail and the copy is current")
    added, dropped = sorted(real - mine), sorted(mine - real)
    parts = []
    if added:
        parts.append(f"shellparse defers {added} which this file does not list, so those denials "
                     f"would be counted as POLICY and could never fail the invariant")
    if dropped:
        parts.append(f"this file lists {dropped} which shellparse no longer defers, so the checks "
                     f"assert a deferral that does not happen")
    return Result(name, FAIL, "; ".join(parts))


@check("vendor_examples_are_not_shape_denied", "static")
def _vendor_examples_are_not_shape_denied():
    name = "vendor_examples_are_not_shape_denied"
    cmds, mans = _ve_harvest()
    if not cmds:
        return Result(name, NA,
                      f"no Salesforce CLI examples found ({mans} manifest(s) readable) — there is "
                      f"no corpus to measure, which is not the same as a corpus with no denials")

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
        except Exception as e:                               # noqa: BLE001
            k = f"CLASSIFIER-ERROR:{type(e).__name__}"
            shape[k] = shape.get(k, 0) + 1
            continue
        if r.get("defer"):
            deferred += 1
        elif r.get("deny"):
            code = r["deny"][1]
            b = shape if code in _SHAPE_CODES else policy
            b[code] = b.get(code, 0) + 1
        elif any(not shellparse.targets(a)
                 for a in (r.get("writes") or []) + (r.get("mutations") or [])):
            policy["no-explicit-target"] = policy.get("no-explicit-target", 0) + 1
        else:
            allowed += 1

    n = len(cmds)
    codes = ", ".join(f"{k}={v}" for k, v in sorted(policy.items())) or "none"
    summary = (f"{n} vendor examples from {mans} manifest(s): {allowed} allowed, "
               f"{deferred} deferred, {sum(policy.values())} policy-denied")

    if not shim:
        # Same honesty as its sibling: without a shim the shape classes deny by design, so the
        # invariant is untestable and saying so beats a green measured in the wrong configuration.
        return Result(name, WARN,
                      f"no verified shim on PATH, so shape denials are expected and the invariant "
                      f"is untestable here. {summary}, {sum(shape.values())} shape-denied. "
                      f"policy: {codes}")
    if shape:
        worst = ", ".join(f"{k}={v}" for k, v in sorted(shape.items()))
        return Result(name, FAIL,
                      f"a verified shim is on PATH, so nothing may be refused for being "
                      f"unreadable — but {sum(shape.values())} of {n} commands SALESFORCE ITSELF "
                      f"PUBLISHES were: {worst}. Copy-paste from the vendor's own documentation "
                      f"is being refused for a parser limit.")
    return Result(name, PASS,
                  f"verified shim; zero shape denials against the vendor's own published "
                  f"examples. {summary}. policy denials: {codes}")


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
            bucket = shape if code in _SHAPE_CODES else policy
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
