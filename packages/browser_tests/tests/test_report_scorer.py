from __future__ import annotations
import sys
from pathlib import Path
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json, os, tempfile
from jsc_browser_tests.report import score_run, exit_code
from jsc_browser_tests.report import render_md
from jsc_browser_tests import manifest as _manifest
from jsc_browser_tests.runner import FlowResult

def _cell(status, flow="e2e_conflict_check", is_happy=True, is_crud=False):
    fr = FlowResult(flow_name=flow, profile="admin", target_org="sf-fake", overall_status=status)
    fr.side_effects = {"is_happy": is_happy, "is_crud": is_crud}
    return fr

def test_all_pass_scores_100():
    assert score_run([_cell("PASS"), _cell("PASS")])["score"] == 100

def test_happy_fail_is_p0_forces_zero():
    s = score_run([_cell("FAIL", is_happy=True)])
    assert s["p0"] == 1 and s["score"] == 0

def test_negative_variation_fail_is_p1_minus5():
    s = score_run([_cell("PASS"), _cell("FAIL", is_happy=False)])
    assert s["p1"] == 1 and s["score"] == 95

def test_not_applicable_excluded_from_denominator():
    s = score_run([_cell("PASS"), _cell("NOT_APPLICABLE")])
    assert s["score"] == 100 and s["counted"] == 1

def test_skip_excluded_from_denominator():
    s = score_run([_cell("PASS"), _cell("SKIP")])
    assert s["score"] == 100 and s["counted"] == 1

def test_exit_code_precedence():
    assert exit_code(score={"p0":0,"score":100,"counted":1}, write_gate_ok=True, teardown_leak=False) == 0
    assert exit_code(score={"p0":0,"score":90}, write_gate_ok=True, teardown_leak=False) == 2
    assert exit_code(score={"p0":1,"score":0}, write_gate_ok=True, teardown_leak=False) == 3
    assert exit_code(score={"p0":1,"score":0}, write_gate_ok=True, teardown_leak=True) == 4
    assert exit_code(score={"p0":0,"score":100}, write_gate_ok=False, teardown_leak=False) == 5

def test_render_md_has_grid_and_score():
    c = _cell("PASS")
    c.flow_name = "e2e_conflict_check"
    md = render_md([c], {"score": 100, "p0": 0, "p1": 0, "p2": 0, "counted": 1})
    assert "| Flow | Profile | Status |" in md
    assert "Score: 100" in md

def test_manifest_round_trips_and_audit_is_bounded():
    c = _cell("PASS")
    c.side_effects = {"rows": [{"Id": "001TEST"}]}
    d = tempfile.mkdtemp(prefix="jsc-suite-test-")
    mpath = os.path.join(d, "run-manifest.json")
    alog = os.path.join(d, "audit.log")
    _manifest.write(
        mpath, [c], {"score": 100, "p0": 0, "p1": 0, "p2": 0, "counted": 1},
        [{"iso": "2026-06-01", "flow": "e2e_conflict_check", "status": "PASS",
          "secret": "DO_NOT_LOG"}],
        audit_log=alog,
    )
    back = json.loads(open(mpath, encoding="utf-8").read())
    assert back["score"]["score"] == 100
    assert back["cells"][0]["test_record_ids"] == ["001TEST"]
    line = open(alog, encoding="utf-8").read()
    assert "DO_NOT_LOG" not in line and "secret" not in line   # PII/unknown fields dropped
    assert "PASS" in line                                       # bounded fields kept
    assert oct(os.stat(alog).st_mode)[-3:] == "600"            # operator-private

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
