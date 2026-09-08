"""Generation is prepared evidence, not a passing live QA surface."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from jsc_qa import dispatcher, report
from jsc_probes.cli import main as generate


@pytest.fixture
def setup_probe(tmp_path, monkeypatch):
    monkeypatch.setenv("TORQUE_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("JSC_QA_ADV_PROBE_OUT", str(tmp_path / "output"))
    monkeypatch.delenv("JSC_QA_ADV_PROBE_MAX_FILES", raising=False)
    monkeypatch.delenv("JSC_QA_ADV_PROBE_BUDGET_S", raising=False)
    source = tmp_path / "Example.cls"
    source.write_text("public class Example { public static String echo(String value){return value;} }")
    calls = []
    def local_only(argv, **kwargs):
        assert argv[:3] == [sys.executable, "-m", "jsc_probes.cli"]
        calls.append(argv)
        return SimpleNamespace(returncode=generate(argv[3:]), stdout="local generated draft", stderr="")
    monkeypatch.setattr(dispatcher.subprocess, "run", local_only)
    return source, calls


def test_generated_draft_cannot_make_qa_pass(setup_probe):
    source, calls = setup_probe
    result = dispatcher.dispatch_adv_probe("unresolved-synthetic-org", str(source))
    assert len(calls) == 1
    assert result.status == "MANUAL_REQUIRED"
    assert report.result_exit_code([result]) == 3
    assert {key: result.metadata[key] for key in ("generated", "compiled", "executed")} == {"generated": True, "compiled": False, "executed": False}
    assert result.metadata["generated_count"] == result.metadata["identified_count"] == 1
    draft = Path(result.metadata["generated_files"][0]).read_text()
    assert "Example.echo((String)null)" in draft
    assert "System.assert(false, 'DRAFT:" in draft


def test_truncated_drafts_remain_incomplete(setup_probe, monkeypatch):
    source, calls = setup_probe
    second = source.with_name("Second.cls")
    second.write_text("public class Second { public static void run(){} }")
    monkeypatch.setenv("JSC_QA_ADV_PROBE_MAX_FILES", "1")
    result = dispatcher.dispatch_adv_probe("unresolved-synthetic-org", f"{source} {second}")
    assert len(calls) == 1
    assert result.status == "MANUAL_REQUIRED"
    assert report.result_exit_code([result]) == 3
    assert result.metadata["identified_count"] == 2
    assert result.metadata["generated_count"] == result.metadata["dropped_count"] == 1


def test_exit_zero_without_output_is_an_error(setup_probe, monkeypatch):
    source, _ = setup_probe
    monkeypatch.setattr(dispatcher.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    result = dispatcher.dispatch_adv_probe("unresolved-synthetic-org", str(source))
    assert result.status == "ERROR"
    assert result.metadata["generated"] is False
    assert result.metadata["compiled"] is result.metadata["executed"] is False


def test_missing_metadata_is_an_error(setup_probe, monkeypatch):
    source, _ = setup_probe
    def missing_companion(argv, **kwargs):
        output = Path(argv[-1])
        (output / "ExampleAdversarialTest.cls").write_text("synthetic incomplete output")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(dispatcher.subprocess, "run", missing_companion)
    assert dispatcher.dispatch_adv_probe("unresolved-synthetic-org", str(source)).status == "ERROR"


def test_edited_draft_survives_repeated_dispatch(setup_probe):
    source, _ = setup_probe
    first = dispatcher.dispatch_adv_probe("unresolved-synthetic-org", str(source))
    draft = Path(first.metadata["generated_files"][0])
    draft.write_text("retained business expectations")
    second = dispatcher.dispatch_adv_probe("unresolved-synthetic-org", str(source))
    assert second.status == "FAIL"
    assert second.metadata["generated"] is False
    assert draft.read_text() == "retained business expectations"


def test_no_sources_never_becomes_passing_qa(setup_probe):
    _, calls = setup_probe
    result = dispatcher.dispatch_adv_probe("unresolved-synthetic-org", "No source files")
    assert result.status == "MANUAL_REQUIRED"
    assert report.result_exit_code([result]) == 3
    assert calls == []


def test_explicit_private_codebase_output_has_no_legacy_shield(setup_probe, monkeypatch):
    source, calls = setup_probe
    output = source.parent / "codebase" / "drafts"
    monkeypatch.setenv("JSC_QA_ADV_PROBE_OUT", str(output))
    result = dispatcher.dispatch_adv_probe("unresolved-synthetic-org", str(source))
    assert result.status == "MANUAL_REQUIRED"
    assert len(calls) == len(list(output.rglob("*AdversarialTest.cls"))) == 1
