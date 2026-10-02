import json

import pytest

from jsc_browser_tests import diagnostics, manifest


@pytest.mark.parametrize("key", ["sfdxAuthUrl", "sfdx_auth_url", "SFDXAUTHURL", "authCode", "auth_code", "oauthToken", "frontdoorUrl", "csrfToken"])
def test_same_secret_keys_in_nested_objects_and_serialized_text(key):
    value = {"nested": [{key: "synthetic-credential-value"}]}
    for output in (diagnostics.redact(value), diagnostics.redact(json.dumps(value)), diagnostics.redact(repr(value))):
        assert "synthetic-credential-value" not in str(output)


def test_auth_url_redacted_without_a_key():
    assert "synthetic-credential-value" not in diagnostics.redact(
        "failed with force://client:synthetic-credential-value@example.org")


@pytest.mark.parametrize("value", [123456, {"value": "synthetic-credential-value"}, ["synthetic-credential-value"]])
def test_serialized_json_secret_values_are_redacted_regardless_of_type(value):
    output = json.loads(diagnostics.redact(json.dumps({"nested": [{"authCode": value}]})))
    assert output["nested"][0]["authCode"] == "[REDACTED]"


def test_manifest_and_audit_never_persist_structured_credentials(tmp_path):
    path, audit = tmp_path / "run.json", tmp_path / "audit.jsonl"
    manifest.write(path, [], {}, [{"flow": {"authCode": "synthetic-credential-value"}}],
                   audit_log=audit, extra={"adapter": [{"sfdxAuthUrl": "synthetic-credential-value"}]})
    assert "synthetic-credential-value" not in path.read_text(encoding="utf-8")
    assert "synthetic-credential-value" not in audit.read_text(encoding="utf-8")


def test_console_redacts_adapter_errors_and_labels(capsys):
    from types import SimpleNamespace
    from jsc_browser_tests.cli import _print_flow_result
    value = {"authCode": "synthetic-credential-value"}
    _print_flow_result(SimpleNamespace(flow_name=value, profile="synthetic", overall_status="FAIL",
        duration_seconds=0, error={"nested": [value]}, steps=[SimpleNamespace(
            status="FAIL", fidelity="API", step_name=value, duration_seconds=0, detail=str(value))]))
    assert "synthetic-credential-value" not in capsys.readouterr().out


def test_sf_error_redacts_before_truncating(monkeypatch):
    import subprocess
    from types import SimpleNamespace
    from jsc_browser_tests.sf_client import SfClient, SfError
    value = "synthetic-credential-value" * 30
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1,
        stderr=json.dumps({"authCode": value})))
    with pytest.raises(SfError) as raised:
        SfClient("synthetic")._run(["data", "query"])
    assert "synthetic-credential-value" not in str(raised.value)
