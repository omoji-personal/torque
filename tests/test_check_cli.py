"""`torque check metadata` through the CLI: JSON schema, exit codes, ignores, config, lessons hook, help."""
import contextlib
import io
import json
from pathlib import Path
import sys
import types

import pytest

from check_fixtures import e10_project, Tree
import jsc_memory
from jsc_loganalyzer import code_analyzer as adapter
from torque import cli
from torque.checks import rules

SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "docs" / "check-metadata.schema.json")
                    .read_text(encoding="utf-8"))


def run(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


def validate(value, schema, where="$"):
    """The subset of JSON Schema the documented schema uses: type, enum, required, properties, items."""
    kinds = schema.get("type")
    if kinds:
        kinds = kinds if isinstance(kinds, list) else [kinds]
        py = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool, "null": type(None)}
        ok = any(isinstance(value, py[k]) and not (k == "integer" and isinstance(value, bool)) for k in kinds)
        assert ok, f"{where}: {value!r} is not {kinds}"
    if "enum" in schema:
        assert value in schema["enum"], f"{where}: {value!r} not in {schema['enum']}"
    if isinstance(value, dict):
        for key in schema.get("required", []):
            assert key in value, f"{where}: missing {key}"
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                validate(value[key], sub, f"{where}.{key}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            validate(item, schema["items"], f"{where}[{i}]")


@pytest.fixture
def no_analyzer(monkeypatch):
    def missing(*a, **k):
        raise adapter.NotInstalled("sf CLI not found on PATH; " + adapter.INSTALL_HINT)
    monkeypatch.setattr(adapter, "run_code_analyzer", missing)


def test_json_round_trip_matches_the_documented_schema(tmp_path, no_analyzer):
    t = e10_project(tmp_path)
    code, out, err = run("check", "metadata", str(t.root), "--json")
    assert code == 0, err
    report = json.loads(out)
    validate(report, SCHEMA)
    assert report["findings"] and all(f["rule"] and f["severity"] and f["action"] for f in report["findings"])
    assert json.loads(json.dumps(report)) == report


def test_text_report_first_and_last_lines(tmp_path, no_analyzer):
    t = e10_project(tmp_path)
    code, out, _ = run("check", "metadata", str(t.root))
    lines = out.strip().splitlines()
    assert code == 0 and lines[0].startswith("Scope: everything under")
    assert lines[-1].startswith("Totals: 1 error(s)") and "Not checked:" in lines[-1]
    assert "Code Analyzer: not installed, so its rules did not run" in out
    assert "sf plugins install code-analyzer" in out
    assert out.index("access.field-without-grant") < out.index("flow.no-fault-path")   # most severe first


def test_exit_codes(tmp_path, no_analyzer):
    t = e10_project(tmp_path)
    assert run("check", "metadata", str(t.root), "--fail-on", "error")[0] == 1
    assert run("check", "metadata", str(t.root), "--fail-on", "error", "--ignore", "access.field-without-grant")[0] == 0
    assert run("check", "metadata", str(t.root), "--fail-on", "warning", "--ignore", "access.field-without-grant")[0] == 1
    code, _, err = run("check", "metadata", str(t.root), "--analyzer", "required")
    assert code == 3 and "--analyzer required" in err
    code, _, err = run("check", "metadata", str(t.root), "--ignore", "no.such-rule")
    assert code == 2 and "unknown rule" in err
    code, _, err = run("check", "metadata", str(tmp_path / "missing"))
    assert code == 2 and "no such file or folder" in err


def test_fail_on_skips_inherited_findings(tmp_path, no_analyzer):
    before, after = Tree(tmp_path / "b"), Tree(tmp_path / "a")
    before.field("Shift__c", "Hours__c", desc=None, help="h")
    after.field("Shift__c", "Hours__c", desc=None, help="changed help")
    code, out, _ = run("check", "metadata", str(after.root), "--baseline", str(before.root), "--fail-on", "warning",
                       "--json")
    report = json.loads(out)
    assert [f["inherited"] for f in report["findings"]] == [True] and code == 0


def test_config_file_ignores_with_reasons(tmp_path, no_analyzer):
    t = e10_project(tmp_path)
    (t.root / "torque-check.yml").write_text(
        "ignore:\n  - rule: doc.help-text-missing\n    reason: help text lives on the page layout\n"
        "  - path: force-app/main/default/flows/*\n    reason: the flow is reviewed separately\n", encoding="utf-8")
    code, out, _ = run("check", "metadata", str(t.root), "--json")
    report = json.loads(out)
    assert code == 0 and report["ignored"]["components"] == 1
    assert report["ignored"]["findings_dropped"] == {"doc.help-text-missing": 1}
    assert not [f for f in report["findings"] if f["component"]["type"] == "Flow"]
    (t.root / "torque-check.yml").write_text("ignore:\n  - rule: doc.help-text-missing\n", encoding="utf-8")
    code, _, err = run("check", "metadata", str(t.root))
    assert code == 2 and "has no reason" in err


def test_lessons_hook_prints_what_recall_returns(tmp_path, no_analyzer, monkeypatch):
    t = e10_project(tmp_path)
    seen = {}

    def for_command(text, **kw):
        seen.update(kw, text=text)
        return "L-1234 Grant new fields in the same deploy."

    fake = types.ModuleType("jsc_memory.recall")
    fake.for_command = for_command
    monkeypatch.setitem(sys.modules, "jsc_memory.recall", fake)
    monkeypatch.setattr(sys.modules["jsc_memory"], "recall", fake, raising=False)
    code, out, _ = run("check", "metadata", str(t.root))
    assert code == 0 and "From earlier work (lessons):" in out and "L-1234" in out
    assert "CustomField" in seen["types"] and "Flow" in seen["types"]

    def broken(*a, **k):
        raise RuntimeError("lessons store unreadable")
    fake.for_command = broken
    code, out, _ = run("check", "metadata", str(t.root), "--json")
    assert code == 0 and json.loads(out)["lessons"] is None


def test_help_lists_every_rule(capsys):
    with pytest.raises(SystemExit):
        cli.main(["check", "metadata", "--help"])
    text = capsys.readouterr().out
    for rule in rules.RULES:
        assert rule in text
    assert "--changed-from" in text and "Exit:" in text


def test_docs_name_every_rule():
    docs = (Path(__file__).resolve().parents[1] / "docs" / "checks.md").read_text(encoding="utf-8")
    missing = [rule for rule in rules.RULES if f"`{rule}`" not in docs]
    assert not missing
