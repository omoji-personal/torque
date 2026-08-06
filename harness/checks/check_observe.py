# Observe-only checks. Observe-only is a bypass by construction: while the window is open a
# denial becomes a log line and the operation proceeds. Two properties make that measurable
# rather than reckless, and this file exists so both fail loudly if they ever stop holding.
#
#   1. The window is honoured only when signed with the anchor secret, unexpired, and bound to
#      THIS tree. Absent, forged, expired or wrong-tree must all leave the gate refusing exactly
#      as it would with no window at all — otherwise the agent can write its own.
#   2. It NEVER applies to a denial that protects the trust boundary. A window that could permit
#      an anchor read could go looking for the secret that mints windows.
#
# Every case builds a THROWAWAY anchor and redirects TORQUE_ANCHOR and TORQUE_AUDIT_LOG at it,
# so none of this reads or writes the operator's real anchor or their real audit trail — which
# matters because the suite is frequently run inside a live window.
import json as _oj
import os as _oos
import shutil as _osh
import subprocess as _osp
import sys as _osys
import tempfile as _otf
import time as _otime
from pathlib import Path as _OP

_OGATE = "hooks/prod_write_gate.py"

# AN OBSERVABLE REFUSAL: a destructive operation with no approval token, refused for want of the
# token rather than for the org's classification. Exactly the friction an operator opens a window
# to count.
_ORG_EVENT = {
    "tool_name": "Bash",
    "tool_input": {"command": "sf data delete bulk --sobject Widget__c --file ids.csv "
                              "--target-org sf-observe-probe"},
}
_ORG_GATE = "hooks/destructive_data_gate.py"
# A trust-boundary refusal: must stay a refusal no matter what window is open.
_ANCHOR_EVENT = {"tool_name": "Bash", "tool_input": {"command": "cat ~/.torque/secret"}}
# A PRODUCTION refusal. An unauthenticated alias classifies production, which is the fail-safe
# direction, so this reaches the production denial without needing a live org.
#
# This file previously used a production write as its OBSERVABLE case and passed — which was the
# defect, not the test: it asserted that a production denial becomes advisory under a window. It
# must not. An observe grant is bound to the TREE, and the shim runs the gates with TORQUE_HOME
# set to the recorded repo from any directory, so a window opened to measure sandbox friction
# would otherwise make every production write on the machine advisory for its duration.
_PROD_EVENT = {
    "tool_name": "Bash",
    "tool_input": {"command": "sf data update record --sobject Account --record-id 001abc "
                              "--values X=1 --target-org acme-prod"},
}


class _ObserveAnchor:
    """A throwaway anchor, optionally holding an observe grant in a chosen state of brokenness."""

    def __init__(self, *, mint=True, tree=None, exp_delta=600, valid_sig=True):
        self.dir = _OP(_otf.mkdtemp(prefix="torque-observe-"))
        self.anchor = self.dir / "anchor"
        self.audit = self.dir / "audit.log"
        self.anchor.mkdir(parents=True, exist_ok=True)
        _oos.chmod(self.anchor, 0o700)
        self.secret = self.anchor / "secret"
        self.secret.write_bytes(_oos.urandom(32))
        _oos.chmod(self.secret, 0o600)
        if mint:
            g = {"tree": tree or str(ROOT.resolve()),
                 "exp": int(_otime.time()) + exp_delta, "iat": int(_otime.time())}
            g["sig"] = self._sign(g) if valid_sig else "0" * 64
            (self.anchor / "observe.grant").write_text(_oj.dumps(g))
            _oos.chmod(self.anchor / "observe.grant", 0o600)

    def _sign(self, payload):
        import hashlib as _oh
        import hmac as _ohm
        body = _oj.dumps(payload, sort_keys=True).encode()
        return _ohm.new(self.secret.read_bytes(), body, _oh.sha256).hexdigest()

    def env(self):
        e = dict(_oos.environ)
        e["TORQUE_ANCHOR"] = str(self.anchor)
        e["TORQUE_AUDIT_LOG"] = str(self.audit)
        e.pop("TORQUE_SHIM_DEPTH", None)
        return e

    def run(self, event, gate=_OGATE):
        r = _osp.run([_osys.executable, str(ROOT / gate)], input=_oj.dumps(event),
                     capture_output=True, text=True, env=self.env())
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    def trail(self):
        return self.audit.read_text() if self.audit.exists() else ""

    def close(self):
        _osh.rmtree(self.dir, ignore_errors=True)


@check("observe_grant_is_operator_only", "static", catastrophe=True)
def _observe_grant_is_operator_only():
    """Only a correctly-signed, unexpired, this-tree window turns a denial into an observation.

    Four negatives and one positive, and the negatives are the security property. The positive
    asserts the REASON rather than just the exit code: a gate that allowed the operation for some
    unrelated reason would satisfy `exit == 0` while proving nothing, which is the trap this
    repo has already fallen into once.
    """
    cases = []
    a = _ObserveAnchor(mint=False)
    try:
        code, out = a.run(_ORG_EVENT, _ORG_GATE)
        cases.append(("no window still denies", code == 2 and "DENY" in out, code))
    finally:
        a.close()

    for label, kw in (("forged signature", {"valid_sig": False}),
                      ("expired window", {"exp_delta": -60}),
                      ("window for another tree", {"tree": "/somewhere/else"})):
        b = _ObserveAnchor(**kw)
        try:
            code, out = b.run(_ORG_EVENT, _ORG_GATE)
            cases.append((label, code == 2 and "OBSERVE-ONLY" not in out, code))
        finally:
            b.close()

    c = _ObserveAnchor()
    try:
        code, out = c.run(_ORG_EVENT, _ORG_GATE)
        cases.append(("valid window records and allows",
                      code == 0 and "OBSERVE-ONLY" in out and "would have denied" in out, code))
    finally:
        c.close()

    bad = [f"{n} (exit {x})" for n, ok, x in cases if not ok]
    if bad:
        return Result("observe_grant_is_operator_only", FAIL,
                      "observe-only window honoured when it must not be, or ignored when it "
                      "must be: " + "; ".join(bad))
    return Result("observe_grant_is_operator_only", PASS,
                  f"{len(cases)} cases: absent/forged/expired/wrong-tree all still deny; a valid "
                  f"window records and allows")


@check("observe_never_opens_the_trust_boundary", "static", catastrophe=True)
def _observe_never_opens_the_trust_boundary():
    """An open window must not reach the anchor, and must write down what it let through.

    These are one check because they are one argument. Observe-only is defensible only if the
    thing it cannot do is reach the secret that mints it, and only if everything it DOES let
    through is recoverable afterwards from the trail. An observation nobody can read later is
    indistinguishable from the gate having been switched off.
    """
    a = _ObserveAnchor()
    try:
        code, out = a.run(_ANCHOR_EVENT)
        anchor_held = code == 2 and "DENY" in out and "OBSERVE-ONLY" not in out

        # PRODUCTION is the second thing a window must never make advisory, and for a reason the
        # anchor case does not cover: the grant is tree-bound, not org-bound, and the shim runs
        # the gates with TORQUE_HOME set to the recorded repo from ANY directory. So without this
        # exclusion, a window opened to measure sandbox friction makes every production write on
        # the machine advisory for its duration.
        pcode, pout = a.run(_PROD_EVENT)
        prod_held = pcode == 2 and "DENY" in pout and "OBSERVE-ONLY" not in pout

        code2, _ = a.run(_ORG_EVENT, _ORG_GATE)
        trail = a.trail()
        recorded = '"OBSERVE"' in trail and "WOULD DENY" in trail

        # The org operation that was let through must be findable in the trail by an auditor who
        # was not present. Both halves matter: the decision word and what it would have refused.
        if not anchor_held:
            return Result("observe_never_opens_the_trust_boundary", FAIL,
                          f"an open observe-only window allowed a trust-anchor read "
                          f"(exit {code}) — the window would grant access to the secret that "
                          f"mints windows")
        if not prod_held:
            return Result("observe_never_opens_the_trust_boundary", FAIL,
                          f"an open observe-only window made a PRODUCTION write advisory "
                          f"(exit {pcode}) — the grant is tree-bound and the shim carries it to "
                          f"every directory, so this is machine-wide, not workspace-scoped")
        if not recorded:
            return Result("observe_never_opens_the_trust_boundary", FAIL,
                          f"observed operation exited {code2} but the audit trail carries no "
                          f"OBSERVE record ({len(trail)} bytes) — an unrecorded observation is "
                          f"the gate switched off")
        return Result("observe_never_opens_the_trust_boundary", PASS,
                      "under an open window the trust anchor and PRODUCTION both stay denied, "
                      "and every observation is written to the durable trail before the "
                      "operation proceeds")
    finally:
        a.close()
