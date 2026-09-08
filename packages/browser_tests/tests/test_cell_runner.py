from __future__ import annotations
import sys
from pathlib import Path
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jsc_browser_tests.runner import StepResult

def test_stepresult_v2_has_side_effects_and_new_statuses():
    sr = StepResult(step_name="s", status="NOT_APPLICABLE", fidelity="USER_FIDELITY")
    assert sr.side_effects == {}              # new field, defaults empty
    sr2 = StepResult(step_name="s2", status="WARN", fidelity="USER_FIDELITY",
                     side_effects={"rows": 1})
    assert sr2.status == "WARN"
    assert sr2.side_effects["rows"] == 1

import asyncio
from jsc_browser_tests.cell_runner import run_cell
from jsc_browser_tests.flow_spec import FlowSpec, FlowCtx, Variation
from jsc_browser_tests.runner import BaseFlow, StepResult
from jsc_browser_tests.sf_client import FakeSfClient

class _StubFlow(BaseFlow):
    spec = FlowSpec(name="stub", workflow="E3", variations=[Variation("happy")])
    name = "stub"
    def __init__(self): self.calls = []
    async def provision(self, ctx): self.calls.append("provision"); return {"id": "001x"}
    async def run(self, page, ctx, variation): self.calls.append("run"); return [StepResult("s","PASS","USER_FIDELITY")]
    async def verify_side_effects(self, ctx): self.calls.append("verify"); return {"ok": True}
    async def teardown(self, ctx): self.calls.append("teardown")

def _ctx():
    return FlowCtx(target_org="sf-fake", instance_url="https://fake", org_id_18="00Dfake",
                   runid="TEST-r1", run_dir=Path("/tmp"), variation=Variation("happy"),
                   profile="admin", values={}, seed={}, handles={}, page=object(),
                   sf=FakeSfClient())

def test_cell_runs_full_lifecycle_in_order():
    flow = _StubFlow()
    res = asyncio.run(run_cell(flow, _ctx()))
    assert flow.calls == ["provision", "run", "verify", "teardown"]
    assert res.overall_status == "PASS"

def test_cell_tears_down_even_when_run_raises():
    class _Boom(_StubFlow):
        async def run(self, page, ctx, variation):
            self.calls.append("run"); raise RuntimeError("kaboom")
    flow = _Boom()
    res = asyncio.run(run_cell(flow, _ctx()))
    assert "teardown" in flow.calls           # teardown still ran
    assert res.overall_status == "FAIL"
    assert "kaboom" in (res.error or "")

class _GatedFlow(BaseFlow):
    spec = FlowSpec(name="gated", workflow="E7", requires=["experience_cloud_enabled"],
                    variations=[Variation("happy")])
    name = "gated"
    def __init__(self): self.calls = []
    async def provision(self, ctx): self.calls.append("provision"); return {}
    async def run(self, page, ctx, variation): self.calls.append("run"); return []
    async def verify_side_effects(self, ctx): return {}
    async def teardown(self, ctx): self.calls.append("teardown")

def test_cell_skips_when_precondition_absent():
    flow = _GatedFlow()
    res = asyncio.run(run_cell(flow, _ctx()))  # FakeSfClient has no Network rows
    assert res.overall_status == "SKIP"
    assert "provision" not in flow.calls          # gated BEFORE provision (nothing created)
    assert "experience_cloud_enabled" in str(res.side_effects)

def test_cell_proceeds_when_precondition_met():
    flow = _GatedFlow()
    ctx = _ctx()
    ctx.sf = FakeSfClient(query_results={"SELECT Id FROM Network LIMIT 1": [{"Id": "0DBx"}]})
    res = asyncio.run(run_cell(flow, ctx))
    assert res.overall_status == "INCOMPLETE"  # capability exists, but no browser steps were observed
    assert "provision" in flow.calls and "run" in flow.calls

class _BadTokenFlow(_GatedFlow):
    spec = FlowSpec(name="badtok", workflow="E7", requires=["totally_made_up"],
                    variations=[Variation("happy")])
    name = "badtok"

def test_cell_fails_on_unknown_precondition_token():
    flow = _BadTokenFlow()
    res = asyncio.run(run_cell(flow, _ctx()))
    assert res.overall_status == "FAIL"
    assert "totally_made_up" in (res.error or "")

class _VerifyFailFlow(_StubFlow):
    """All run() steps PASS, but verify reports a "*_check": "FAIL" assertion."""
    spec = FlowSpec(name="verifyfail", workflow="E3", variations=[Variation("happy")])
    name = "verifyfail"
    async def run(self, page, ctx, variation):
        return [StepResult("clicked", "PASS", "USER_FIDELITY")]
    async def verify_side_effects(self, ctx):
        return {"shift_check": "FAIL", "new_shifts": 0}

def test_cell_fails_when_verify_dict_reports_FAIL():
    # regression: a flow whose only assertion is a verify-dict "*_check": "FAIL"
    # (run steps all PASS) MUST fail the cell, not silently pass.
    res = asyncio.run(run_cell(_VerifyFailFlow(), _ctx()))
    assert res.overall_status == "FAIL", res.overall_status
    assert "shift_check" in (res.error or "")

def test_cell_passes_when_verify_dict_all_pass():
    # SKIP / non-FAIL verify values must NOT fail the cell.
    class _OkFlow(_StubFlow):
        async def verify_side_effects(self, ctx):
            return {"accept_check": "PASS", "note": "ok", "other_check": "SKIP"}
    res = asyncio.run(run_cell(_OkFlow(), _ctx()))
    assert res.overall_status == "PASS", res.overall_status

def main() -> int:
    failures = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try: fn(); print(f"PASS: {name}")
            except (AssertionError, TypeError) as e: failures += 1; print(f"FAIL: {name}: {e}")
    print(f"\n{'ALL PASS' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(main())
