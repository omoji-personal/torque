# The completion gate: can the word "done" be withheld, and can it be earned?
#
# The tool's whole claim is that an unobserved layer is counted as unobserved. Every way that
# claim could quietly fail is a way a partial check reads as a complete one:
#
#   · a layer with no evidence reporting VERIFIED
#   · a layer being dropped from the denominator instead of counted against it
#   · the BLOCKED browser layer being waved away by the person it exists to stop
#   · the printed verdict disagreeing with the numbers printed beside it
#   · DONE being unreachable, which makes the verdict carry no information at all
#
# The last one is not a safety failure and matters just as much. A gate that can only ever say
# no is not a gate, it is a wall, and people take walls down.
#
# These run offline. `--target-org` points at an org that does not resolve, so the org-backed
# layers report NOT VERIFIED for a stated reason and the evidence-backed layers can still be
# driven to green — which is exactly the mix needed to test both directions of the verdict.
import importlib.util as _cg_il
import json as _cg_json
import subprocess as _cg_sp
import sys as _cg_sys

_TOOL = ROOT / "bin" / "torque-done"
NO_SUCH_ORG = "torque-completion-check-no-such-org"
DONE, NOT_DONE, REFUSED = 0, 3, 2


def _run(*args, timeout=240):
    return _cg_sp.run([_cg_sys.executable, str(_TOOL), *args],
                      capture_output=True, text=True, cwd=str(ROOT), timeout=timeout)


def _mod():
    # The loader is named explicitly: bin/torque-done has no .py suffix, so
    # spec_from_file_location infers nothing and hands back a spec whose loader is None.
    import importlib.machinery as _cg_mach
    loader = _cg_mach.SourceFileLoader("torque_done_mod", str(_TOOL))
    spec = _cg_il.spec_from_loader("torque_done_mod", loader)
    m = _cg_il.module_from_spec(spec)
    loader.exec_module(m)
    return m


@check("completion_counts_the_unobserved", "static", catastrophe=True)
def _completion_counts_the_unobserved():
    """No evidence must produce NOT VERIFIED, and the denominator must not move.

    The failure this is written against is subtraction, not lying. Nobody marks an unchecked
    layer green; what happens is the layer stops being printed, and 3 of 3 replaces 3 of 6 while
    every line on screen stays true.
    """
    name = "completion_counts_the_unobserved"
    bare = _run("--target-org", NO_SUCH_ORG, "--field", "Account.Tier__c", "--json")
    if bare.returncode != NOT_DONE:
        return Result(name, FAIL,
                      f"a ledger with nothing observed exited {bare.returncode}, want "
                      f"{NOT_DONE} — {(bare.stderr or bare.stdout or '')[:120]}")
    try:
        led = _cg_json.loads(bare.stdout)
    except Exception:                                      # noqa: BLE001
        return Result(name, FAIL, f"--json did not produce JSON: {bare.stdout[:120]}")
    if led["verdict"] != "NOT DONE":
        return Result(name, FAIL, f"verdict {led['verdict']!r} with nothing observed")
    total = len(led["ledger"])
    if led["layers"] != total:
        return Result(name, FAIL,
                      f"the ledger prints {led['layers']} layers and lists {total}")
    # every layer that has no evidence and no org answer must be non-green
    green = [l for l in led["ledger"] if l["outcome"] in ("VERIFIED", "N/A")]
    if len(green) != led["verified"]:
        return Result(name, FAIL,
                      f"counted {led['verified']} verified, {len(green)} rows are green — the "
                      f"number and the rows it summarises disagree")
    for l in led["ledger"]:
        if l["outcome"] == "VERIFIED":
            return Result(name, FAIL,
                          f"{l['layer']} reports VERIFIED with no evidence and no reachable org")
        if not l["detail"].strip():
            return Result(name, FAIL, f"{l['layer']} is {l['outcome']} with no stated reason")

    # the denominator holds while evidence is added: layers are answered, never removed
    fuller = _run("--target-org", NO_SUCH_ORG, "--field", "Account.Tier__c", "--json",
                  "--permset", "T", "--render-evidence", "seen as Standard User",
                  "--automation-evidence", "flow log", "--uat-evidence", "a caseworker")
    try:
        led2 = _cg_json.loads(fuller.stdout)
    except Exception:                                      # noqa: BLE001
        return Result(name, FAIL, f"--json broke once evidence was supplied: {fuller.stdout[:120]}")
    if len(led2["ledger"]) != total:
        return Result(name, FAIL,
                      f"the denominator moved from {total} to {len(led2['ledger'])} when "
                      f"evidence was supplied — layers must be answered, never removed")
    if led2["verified"] <= led["verified"]:
        return Result(name, FAIL,
                      f"supplying evidence for three layers did not raise the verified count "
                      f"({led['verified']} → {led2['verified']}) — the evidence is not being read")
    return Result(name, PASS,
                  f"{total} layers, none green without evidence, each carrying a stated reason; "
                  f"the denominator holds at {total} as evidence takes the count from "
                  f"{led['verified']} to {led2['verified']}")


@check("completion_blocked_cannot_be_waved", "static", catastrophe=True)
def _completion_blocked_cannot_be_waved():
    """`--na` clears a layer that does not apply. It must not clear one that is BLOCKED.

    The distinction is the whole design. "Not applicable" is a judgement about this change and
    the operator is entitled to make it. "Blocked" is a fact about the tool — the browser render
    is not automated — and letting the same flag clear both would hand the person being graded
    an eraser for the one row they most want gone.
    """
    name = "completion_blocked_cannot_be_waved"
    base = ["--target-org", NO_SUCH_ORG, "--field", "Account.Tier__c"]
    cases = [
        (base + ["--na", "profile_render:I looked at it, honestly"], REFUSED, "BLOCKED",
         "clearing the blocked browser layer"),
        (base + ["--na", "fls"], REFUSED, "reason", "--na with no reason"),
        (base + ["--na", "fls:"], REFUSED, "reason", "--na with an empty reason"),
        (base + ["--na", "no_such_layer:whatever"], REFUSED, "names no layer",
         "--na naming a layer that does not exist"),
        (base + ["--field", "NotDotted"], REFUSED, "Object.Field", "a malformed --field"),
    ]
    bad = []
    for argv, want, needle, label in cases:
        r = _run(*argv)
        if r.returncode != want:
            bad.append(f"{label}: exit {r.returncode}, want {want}")
        elif needle.lower() not in (r.stderr or "").lower():
            bad.append(f"{label}: refused without saying why "
                       f"(wanted {needle!r} in: {(r.stderr or '(silent)').strip()[:70]})")
    # and the legitimate use still works: an UNVERIFIED layer may be declared inapplicable
    ok = _run(*base, "--json", "--na", "human_uat:internal-only field, no user-facing surface")
    try:
        led = _cg_json.loads(ok.stdout)
        row = [l for l in led["ledger"] if l["layer"] == "human_uat"][0]
        if row["outcome"] != "N/A" or "internal-only" not in row["detail"]:
            bad.append(f"a legitimate --na did not take: {row}")
    except Exception as e:                                 # noqa: BLE001
        bad.append(f"a legitimate --na broke the ledger: {type(e).__name__}")
    if bad:
        return Result(name, FAIL, "; ".join(bad))
    return Result(name, PASS,
                  f"{len(cases)} malformed or overreaching uses of --na refused with a stated "
                  f"reason, and a legitimate one records its reason in the ledger")


@check("completion_can_say_done", "static", catastrophe=True)
def _completion_can_say_done():
    """The verdict must be reachable in both directions, or it carries no information.

    Driven at the verdict function with synthetic layers, because the green direction needs an
    org that answers and these checks are offline by design. What is under test here is not the
    org queries — those have their own layer reasons — but whether all-green produces DONE and
    whether a single non-green row is enough to withhold it.
    """
    name = "completion_can_say_done"
    m = _mod()

    class _A:
        target_org, field, json = NO_SUCH_ORG, "Account.Tier__c", True

    def verdict(outcomes):
        layers = [m.Layer(f"l{i}", f"layer {i}", o, "synthetic") for i, o in enumerate(outcomes)]
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = m.render(layers, _A())
        return code, _cg_json.loads(buf.getvalue())

    code, led = verdict([m.VERIFIED] * 4)
    if code != DONE or led["verdict"] != "DONE":
        return Result(name, FAIL,
                      f"four verified layers did not produce DONE (exit {code}, "
                      f"verdict {led['verdict']!r}) — the gate can only ever refuse, and a "
                      f"gate that can only refuse gets removed")
    code, led = verdict([m.VERIFIED, m.NA, m.VERIFIED])
    if code != DONE:
        return Result(name, FAIL, "a layer declared inapplicable with a reason blocked DONE")
    for withhold in (m.UNVERIFIED, m.BLOCKED):
        code, led = verdict([m.VERIFIED, withhold, m.VERIFIED])
        if code != NOT_DONE:
            return Result(name, FAIL,
                          f"a single {withhold} row gave exit {code}, want {NOT_DONE}")
        if led["verdict"] != "NOT DONE":
            return Result(name, FAIL,
                          f"a single {withhold} row still reported {led['verdict']!r}")
    return Result(name, PASS,
                  "all-green produces DONE, N/A with a reason counts as answered, and one "
                  "NOT VERIFIED or BLOCKED row is enough to withhold the word")
