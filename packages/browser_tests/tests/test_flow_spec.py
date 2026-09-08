from __future__ import annotations
import sys
from pathlib import Path
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jsc_browser_tests.flow_spec import FlowSpec, FlowCtx, Variation

def test_flowspec_holds_metadata():
    spec = FlowSpec(name="e2e_conflict_check", workflow="E3",
                    objects=["Demo__Intake__c"], profiles=["admin", "standard"],
                    requires=["npsp_or_bucket_account"], variations=[Variation("happy")])
    assert spec.workflow == "E3"
    assert "standard" in spec.profiles

def test_variation_per_profile_expectation():
    v = Variation("platform_denied", profile="platform", expect="error: insufficient access")
    assert v.profile == "platform"
    assert v.expect.startswith("error:")

def test_flowctx_carries_run_dir_and_target():
    ctx = FlowCtx(target_org="sample-sandbox", instance_url="https://x", org_id_18="00D",
                  runid="TEST-abc123", run_dir=Path("/tmp/run"), variation=Variation("happy"),
                  profile="admin", values={}, seed={}, handles={}, page=None, sf=None)
    assert ctx.runid == "TEST-abc123"
    assert ctx.run_dir == Path("/tmp/run")

def main() -> int:
    failures = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try: fn(); print(f"PASS: {name}")
            except AssertionError as e: failures += 1; print(f"FAIL: {name}: {e}")
    print(f"\n{'ALL PASS' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(main())
