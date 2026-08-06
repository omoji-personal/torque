# Shim-first deferral. When the exec-time shim is verifiably in front of `sf`, a static-shape
# refusal is handed downstream instead of denied — the same authorization, run on argv the kernel
# resolved rather than on a string this layer had to reconstruct.
#
# That is a real widening of what reaches exec, so it earns two obligations, and both are here:
#
#   1. It must NEVER fire without a verified shim. No environment variable, no config flag, no
#      "installed but nothing points at it" — a shim sitting in the anchor with PATH resolving
#      past it enforces nothing, and deferring to it would be deciding nothing at all.
#   2. It must NEVER extend past the shape classes. The shim resolves shell expansion; it does
#      not see a write to a protected file, a read of the trust anchor, or a target injected by
#      --flags-dir, which sf itself expands after the shim has already decided.
#
# The measurement that motivated it: 97.2% of what this classifier refused on six months of real
# commands, it refused because it could not read the string, not because it judged the operation
# unsafe. 706 blocked → 22, and the 22 are policy.
import os as _sos
import shutil as _ssh
import sys as _ssys
import tempfile as _stf
from pathlib import Path as _SP

# One command per class. The shape ones must defer under a shim; the policy ones must not,
# whatever is installed.
_SHAPE = {
    "substitution": 'sf data update record --sobject Account --record-id 001abc '
                    '--values X=1 --target-org $(cat /tmp/org.txt)',
    "indirect-argv0": '$CLI data update record --sobject Account --record-id 001abc '
                      '--values X=1 --target-org acme-prod',
    "interp-sf": 'bash -c "sf data update record --sobject Account --record-id 001abc '
                 '--values X=1 --target-org acme-prod"',
}
_POLICY = {
    "anchor-ref": "cat ~/.torque/secret",
    "protected-write": "echo x > hooks/prod_write_gate.py",
    "flags-dir": "sf data update record --flags-dir /tmp/f --sobject Account "
                 "--record-id 001abc --values X=1",
    "wrapper-sf": "sudo sf data delete record --record-id 001abc --sobject Account "
                  "--target-org acme-prod",
    # A RESOLVED interpreter target is policy, not shape. The shim wraps `sf` and `sfdx`; it never
    # sees `python3`, so deferring this hands the decision to no layer at all. Measured
    # 2026-08-06: with the shim on PATH every shape readonly_gate_honours_exactly_the_list
    # requires refused came back exit 0, and runnable_implies_unwritable reported a script outside
    # TORQUE_HOME authorized against an org. Both were this one deferral.
    "runner-not-authorized": "python3 harness/validate.py --profile capability "
                             "--target-org acme-prod",
    # A path with NOTHING at it, deliberately. The first version of _resolved_runner_target
    # required p.is_file(), so this fell through to interp-sf and deferred — which is a TOCTOU as
    # well as a hole, because the agent can create the file after the gate has decided. The
    # existing entry above cannot catch that regression: validate.py exists, so an is_file()
    # check passes for it. Only a non-existent path distinguishes the two implementations.
    "runner-not-authorized-absent": "python3 /tmp/not-torque.py --target-org acme-prod",
}


class _ShimWorld:
    """A throwaway anchor, with the shim installed and on PATH or neither."""

    def __init__(self, installed):
        self.dir = _SP(_stf.mkdtemp(prefix="torque-shimfirst-"))
        self.anchor = self.dir / "anchor"
        (self.anchor / "shim").mkdir(parents=True)
        _sos.chmod(self.anchor, 0o700)
        self.saved = (_sos.environ.get("PATH", ""), _sos.environ.get("TORQUE_ANCHOR"))
        _sos.environ["TORQUE_ANCHOR"] = str(self.anchor)
        if installed:
            src = ROOT / "bin" / "torque-shim-sf"
            _ssh.copyfile(str(src), str(self.anchor / "shim" / "sf"))
            _sos.chmod(str(self.anchor / "shim" / "sf"), 0o755)
            (self.anchor / "shim" / "home").write_text(str(ROOT) + "\n")
            _sos.environ["PATH"] = f"{self.anchor / 'shim'}:{self.saved[0]}"

    def analyze(self, cmd):
        _ssys.path.insert(0, str(ROOT / "hooks"))
        for m in ("shellparse", "lib"):
            _ssys.modules.pop(m, None)
        import shellparse
        shellparse._SHIM_STATE.clear()
        return shellparse, shellparse.analyze_bash(cmd)

    def close(self):
        path, anchor = self.saved
        _sos.environ["PATH"] = path
        if anchor is None:
            _sos.environ.pop("TORQUE_ANCHOR", None)
        else:
            _sos.environ["TORQUE_ANCHOR"] = anchor
        _ssh.rmtree(self.dir, ignore_errors=True)
        for m in ("shellparse", "lib"):
            _ssys.modules.pop(m, None)


@check("shim_deferral_requires_a_verified_shim", "static", catastrophe=True)
def _shim_deferral_requires_a_verified_shim():
    """Without a shim actually in front of `sf`, every shape class stays a hard deny.

    The control arm for the whole feature. If this passes only because deferral never fires at
    all, the next check catches that — the two are only meaningful together.
    """
    w = _ShimWorld(installed=False)
    try:
        sp, _ = w.analyze("true")
        if sp.shim_enforcing():
            return Result("shim_deferral_requires_a_verified_shim", FAIL,
                          "shim_enforcing() is true with no shim installed — deferral would "
                          "hand decisions to a layer that is not there")
        bad = []
        for code, cmd in list(_SHAPE.items()) + list(_POLICY.items()):
            _, r = w.analyze(cmd)
            if not r.get("deny") or r.get("defer"):
                bad.append(code)
        if bad:
            return Result("shim_deferral_requires_a_verified_shim", FAIL,
                          f"with no shim installed these did not deny: {sorted(bad)}")
        return Result("shim_deferral_requires_a_verified_shim", PASS,
                      f"no shim ⇒ all {len(_SHAPE) + len(_POLICY)} classes still deny; "
                      f"shim_enforcing() false")
    finally:
        w.close()


@check("shim_deferral_stops_at_the_shape_classes", "static", catastrophe=True)
def _shim_deferral_stops_at_the_shape_classes():
    """With a verified shim: shape classes defer, policy classes still deny.

    The second half is the security property. The shim resolves shell expansion and nothing else,
    so a protected-file write, a trust-anchor read, a --flags-dir target (expanded by sf itself,
    after the shim has decided) and a sudo'd invocation (secure_path may drop the shim off PATH
    entirely) must all survive as denials.
    """
    w = _ShimWorld(installed=True)
    try:
        sp, _ = w.analyze("true")
        if not sp.shim_enforcing():
            return Result("shim_deferral_stops_at_the_shape_classes", FAIL,
                          "shim installed and first on PATH but shim_enforcing() is false — "
                          "the deferral can never engage, so the 97% denial tax stands")
        not_deferred, leaked = [], []
        for code, cmd in _SHAPE.items():
            _, r = w.analyze(cmd)
            if not r.get("defer"):
                not_deferred.append(code)
        for code, cmd in _POLICY.items():
            _, r = w.analyze(cmd)
            if not r.get("deny") or r.get("defer"):
                leaked.append(code)
        if leaked:
            return Result("shim_deferral_stops_at_the_shape_classes", FAIL,
                          f"a verified shim turned POLICY denials into deferrals: "
                          f"{sorted(leaked)} — the shim never sees these and would authorize "
                          f"nothing downstream")
        if not_deferred:
            return Result("shim_deferral_stops_at_the_shape_classes", FAIL,
                          f"shape classes still denied under a verified shim: "
                          f"{sorted(not_deferred)}")
        return Result("shim_deferral_stops_at_the_shape_classes", PASS,
                      f"{len(_SHAPE)} shape classes defer to the shim; {len(_POLICY)} policy "
                      f"classes ({', '.join(sorted(_POLICY))}) still deny")
    finally:
        w.close()
