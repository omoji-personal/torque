from argparse import Namespace
import json

import pytest

from jsc_revert.wrappers import _common as common, data_update, data_upsert

RECORD_ID = "001000000000001AAA"


@pytest.fixture
def capture(tmp_path, monkeypatch):
    contexts = []
    class Context:
        def __init__(self, **kwargs):
            self.org = Namespace(is_production=False, alias="synthetic")
            self.snap_dir = tmp_path
            self.manifest = {"phases": {name: {} for name in ("pre_snapshot", "underlying_command", "post_finalize")}}
            contexts.append(self)
        def resolve_org(self): return 0
        def acquire_org_lock(self): return 0
        def init_snapshot_dir(self): pass
        def set_revert_capabilities(self): pass
        def update_phase(self, name, **fields): self.manifest["phases"][name].update(fields)
        def save(self): pass
        def release_lock(self): pass
    monkeypatch.setattr(common, "WrapperContext", Context)
    return contexts


def args(**extra):
    return Namespace(target_org="synthetic", sobject="Account", record_id=RECORD_ID,
                     external_id="External__c=key-1", values="Name='Two words'", **extra)


def upsert_response(created=True):
    return json.dumps({"status": 0, "result": {"statusCode": 200, "body": [
        {"id": RECORD_ID, "created": created, "success": True, "errors": []}]}})


def test_atomic_upsert_carries_external_id_and_quoted_values(capture, monkeypatch):
    monkeypatch.setattr(data_upsert, "_query_by_external_id_tagged", lambda *a: ("not_found", None, None))
    monkeypatch.setattr(data_upsert, "_query_record_by_id", lambda *a: {"Id": RECORD_ID})
    commands, rows = [], {}
    def submit(command, **kwargs):
        commands.append(command)
        assert command[1:4] == ["api", "request", "rest"]
        assert command[command.index("--method") + 1] == "PATCH"
        from pathlib import Path
        body = json.loads(Path(command[command.index("--body") + 1][1:]).read_text(encoding="utf-8"))
        assert body["records"][0] == {"attributes": {"type": "Account"}, "Name": "Two words", "External__c": "key-1"}
        record = body["records"][0]
        created = record["External__c"] not in rows
        rows[record["External__c"]] = record
        return 0, upsert_response(created=created), ""
    monkeypatch.setattr(common, "run_sf_subprocess", submit)
    assert data_upsert.run(args()) == 0
    assert capture[-1].manifest["payload"]["upsert_mode"] == "insert"
    assert data_upsert.run(args()) == 0
    assert capture[-1].manifest["payload"]["upsert_mode"] == "update"
    assert len(commands) == 2 and len(rows) == 1


@pytest.mark.parametrize("values,external", [("External__c=other", "External__c=key-1"),
    ("Name=value", "External__c"), ("Name='closed' Other='unclosed", "External__c=key-1"),
    ("Name=one name=two", "External__c=key-1")])
def test_invalid_or_conflicting_upsert_values_never_submit(capture, monkeypatch, values, external):
    monkeypatch.setattr(common, "run_sf_subprocess", lambda *a, **k: pytest.fail("must not submit"))
    request = args(); request.values = values; request.external_id = external
    assert data_upsert.run(request) != 0


def test_indeterminate_lookup_never_becomes_sandbox_insert(capture, monkeypatch):
    monkeypatch.setattr(data_upsert, "_query_by_external_id_tagged", lambda *a: ("error", None, None))
    monkeypatch.setattr(common, "run_sf_subprocess", lambda *a, **k: pytest.fail("must not submit"))
    assert data_upsert.run(args()) == common.EXIT_PRESNAP_FAILED_PROD


@pytest.mark.parametrize("payload", [{}, {"result": {}}, {"result": {"records": []}},
    {"result": {"records": [], "done": False, "totalSize": 0}}, {"result": {"records": "bad"}}])
def test_incomplete_lookup_is_error(monkeypatch, payload):
    monkeypatch.setattr(common, "run_sf_subprocess", lambda *a, **k: (0, json.dumps(payload), ""))
    assert data_upsert._query_by_external_id_tagged("synthetic", "Account", "External__c", "key")[0] == "error"


@pytest.mark.parametrize("module", [data_update, data_upsert])
def test_postcapture_failure_preserves_successful_mutation(capture, monkeypatch, capsys, module):
    if module is data_update:
        rows = iter([{"Id": RECORD_ID}, None])
        monkeypatch.setattr(module, "_query_record", lambda *a: next(rows))
        response = '{"result":{"success":true}}'
    else:
        monkeypatch.setattr(module, "_query_by_external_id_tagged", lambda *a: ("not_found", None, None))
        monkeypatch.setattr(module, "_query_record_by_id", lambda *a: None)
        response = upsert_response()
    monkeypatch.setattr(common, "run_sf_subprocess", lambda *a, **k: (0, response, ""))
    assert module.run(args()) == common.EXIT_POST_FINALIZE_FAILED
    record = capture[-1].manifest
    assert record["snapshot_status"] == "partial"
    assert record["phases"]["underlying_command"]["status"] == "complete"
    assert record["mutation_succeeded"] is True
    assert "do not retry" in capsys.readouterr().err.lower()


def test_failed_sandbox_precapture_is_not_full_pipeline_success(capture, monkeypatch):
    rows = iter([None, {"Id": RECORD_ID}])
    monkeypatch.setattr(data_update, "_query_record", lambda *a: next(rows))
    monkeypatch.setattr(common, "run_sf_subprocess", lambda *a, **k: (0, '{"result":{"success":true}}', ""))
    assert data_update.run(args()) == common.EXIT_PRESNAP_FAILED_SANDBOX
    assert capture[-1].manifest["snapshot_status"] == "partial"


@pytest.mark.parametrize("module", [data_update, data_upsert])
def test_invalid_mutation_json_is_never_complete(capture, monkeypatch, module):
    monkeypatch.setattr(data_update, "_query_record", lambda *a: {"Id": RECORD_ID})
    monkeypatch.setattr(data_upsert, "_query_by_external_id_tagged", lambda *a: ("not_found", None, None))
    monkeypatch.setattr(common, "run_sf_subprocess", lambda *a, **k: (0, "not JSON", ""))
    assert module.run(args()) == common.EXIT_POST_FINALIZE_FAILED
    assert capture[-1].manifest["mutation_succeeded"] is None
    assert capture[-1].manifest["snapshot_status"] == "partial"


def test_upsert_retains_cli_quotes_backslashes_and_value_types(capture, monkeypatch):
    monkeypatch.setattr(data_upsert, "_query_by_external_id_tagged", lambda *a: ("not_found", None, None))
    monkeypatch.setattr(data_upsert, "_query_record_by_id", lambda *a: {"Id": RECORD_ID})
    def submit(command, **kwargs):
        from pathlib import Path
        body = json.loads(Path(command[command.index("--body") + 1][1:]).read_text(encoding="utf-8"))
        assert body["records"][0] == {"attributes": {"type": "Account"}, "External__c": "true",
            "Name": "Synthetic's record", "Description": "C:\\sample\\folder", "Enabled__c": True,
            "Parent__r": {"Key__c": "synthetic"}}
        return 0, upsert_response(), ""
    monkeypatch.setattr(common, "run_sf_subprocess", submit)
    request = args()
    request.external_id = "External__c=true"
    request.values = '''Name="Synthetic's record" Description=C:\\sample\\folder Enabled__c=true Parent__r='{"Key__c":"synthetic"}' '''
    assert data_upsert.run(request) == 0
