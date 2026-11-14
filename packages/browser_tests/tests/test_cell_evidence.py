"""Every executed cell of a browser run keeps its own evidence.

Before, all cells of a run shared one folder and the manifest named only flow and
profile: a later variation or user overwrote an earlier one's screenshot of the same
name, and two variations of one flow could not be told apart in the manifest."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from jsc_browser_tests.flow_spec import FlowSpec
from jsc_browser_tests.runner import BaseFlow, FlowResult, StepResult, Variation
from jsc_browser_tests.sf_client import FakeSfClient
from jsc_browser_tests.suite import run_suite


class TwoByTwo(BaseFlow):
    name = "save_record"
    spec = FlowSpec(name="save_record", workflow="E1", writes=False, profiles=["admin", "standard"],
                    variations=[Variation("happy"), Variation("negative", expect="error: required")])


def _run(tmp_path):
    seen = []

    async def preflight(config):
        return {}

    async def executor(cell, config):
        folder = Path(config["run_dir"])
        shot = folder / "after_save.png"          # the same logical name in every cell
        shot.write_bytes(f"{cell.variation.name}/{cell.profile}".encode())
        seen.append((config["cell_id"], str(shot)))
        return FlowResult(flow_name=cell.flow.spec.name, profile=cell.profile, target_org="sf-fake",
                          overall_status="PASS",
                          steps=[StepResult("after save", "PASS", "USER_FIDELITY", screenshot_path=str(shot))])

    manifest = tmp_path / "manifest.json"
    report = tmp_path / "report.md"
    config = {"sf": FakeSfClient(), "org_info": SimpleNamespace(detected_org_type="sandbox"), "flows": [TwoByTwo()],
              "profiles": ["admin", "standard"], "manifest_path": str(manifest), "report_md_path": str(report),
              "run_dir": str(tmp_path), "audit_log": str(tmp_path / "audit.log"), "preflight": preflight,
              "cell_executor": executor, "seed": {"users": {"standard": {"user_id": "005000000000002AAA"}}}}
    asyncio.run(run_suite(config))
    return seen, json.loads(manifest.read_text(encoding="utf-8")), report.read_text(encoding="utf-8")


def test_four_cells_keep_four_screenshots_of_the_same_name(tmp_path):
    seen, manifest, _ = _run(tmp_path)
    paths = [path for _, path in seen]
    assert len(set(paths)) == 4, "one folder per cell: nothing is overwritten"
    for cell_id, path in seen:
        _, variation, profile = cell_id.split("/")
        assert Path(path).read_bytes() == f"{variation}/{profile}".encode()
        assert Path(path).name == "after_save.png"


def test_the_manifest_and_the_report_tell_the_cells_apart(tmp_path):
    seen, manifest, report = _run(tmp_path)
    cells = manifest["cells"]
    assert sorted((cell["variation"], cell["profile"]) for cell in cells) == [
        ("happy", "admin"), ("happy", "standard"), ("negative", "admin"), ("negative", "standard")]
    assert len({cell["cell_id"] for cell in cells}) == 4 and len({cell["artifact_dir"] for cell in cells}) == 4
    for cell in cells:
        shot = Path(cell["steps"][0]["screenshot_path"])
        assert shot.parent.name == cell["artifact_dir"], "the evidence sits in the cell's own folder"
        assert cell["cell_id"] == f"save_record/{cell['variation']}/{cell['profile']}"
    assert "| Flow | Variation | Profile | Status |" in report
    assert "| save_record | negative | standard | PASS |" in report and "| save_record | happy | admin | PASS |" in report


def test_cells_that_did_not_run_get_no_folder(tmp_path):
    async def failing_preflight(config):
        return {"standard": "FAIL", "preflight_error": "Login As did not switch user"}

    manifest = tmp_path / "manifest.json"
    config = {"sf": FakeSfClient(), "org_info": SimpleNamespace(detected_org_type="sandbox"), "flows": [TwoByTwo()],
              "profiles": ["admin", "standard"], "manifest_path": str(manifest), "run_dir": str(tmp_path),
              "audit_log": str(tmp_path / "audit.log"), "preflight": failing_preflight,
              "seed": {"users": {"standard": {"user_id": "005000000000002AAA"}}}}
    asyncio.run(run_suite(config))
    cells = json.loads(manifest.read_text(encoding="utf-8"))["cells"]
    assert len(cells) == 4 and all(cell["status"] == "INCOMPLETE" and cell["artifact_dir"] == "" for cell in cells)
    assert {cell["variation"] for cell in cells} == {"happy", "negative"}
