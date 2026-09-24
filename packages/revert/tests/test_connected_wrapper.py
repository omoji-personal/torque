"""Connected mode: a Torque write route re-checks the consumed approval and the live org ID."""
import json
from collections import namedtuple

import pytest

from jsc_revert.wrappers import _common as c

Org = namedtuple("Org", "alias org_id_18 org_id_short is_production detected_org_type")
LIVE = Org("acme-prod", "00D000000000002AAA", "00D000000000002", True, "production")


@pytest.fixture
def connected(monkeypatch, tmp_path):
    firm = tmp_path / "firm"
    client = firm / "clients" / "acme"
    client.mkdir(parents=True)
    (client / "client.json").write_text("{}", encoding="utf-8")
    (firm / "workspace.json").write_text(json.dumps({"schema": "torque.workspace/1", "name": "F",
                                                     "profile": "generic", "ai_access": "connected",
                                                     "approval": "required"}), encoding="utf-8")
    monkeypatch.setenv("TORQUE_WORKSPACE", str(client))
    monkeypatch.delenv(c.APPROVED_PARENT_ENV, raising=False)
    monkeypatch.setattr(c.org_detect, "resolve_org", lambda alias, **k: LIVE)
    return firm


def ctx(command="jsc deploy -o acme-prod -m Flow:X"):
    return c.WrapperContext(operation_type="deploy", target_org="acme-prod", wrapper_command=command)


def test_scope_is_read_from_workspace_json(connected, monkeypatch):
    assert c._connected_scope() == (connected, "acme")
    (connected / "workspace.json").write_text(json.dumps({"ai_access": "connected"}), encoding="utf-8")
    assert c._connected_scope() is None
    monkeypatch.delenv("TORQUE_WORKSPACE")
    assert c._connected_scope() is None


def test_refuses_without_consumed_approval(connected, monkeypatch):
    monkeypatch.setattr(c, "_consumed_approval", lambda *a: None)
    assert ctx().resolve_org() == c.EXIT_NOT_APPROVED


def test_refuses_when_org_id_changed(connected, monkeypatch):
    monkeypatch.setattr(c, "_consumed_approval", lambda *a: {"org_id_18": "00D000000000009AAA"})
    assert ctx().resolve_org() == c.EXIT_NOT_APPROVED


def test_allows_matching(connected, monkeypatch):
    seen = {}

    def found(workspace, client, invocation, org):
        seen.update(workspace=workspace, client=client, org=org)
        return {"org_id_18": "00D000000000002AAA"}
    monkeypatch.setattr(c, "_consumed_approval", found)
    assert ctx().resolve_org() == 0
    assert seen == {"workspace": connected, "client": "acme", "org": "acme-prod"}


def test_dry_run_is_exempt(connected, monkeypatch):
    monkeypatch.setattr(c, "_consumed_approval", lambda *a: None)
    assert ctx("jsc deploy -o acme-prod -m Flow:X --dry-run").resolve_org() == 0


def test_firm_root_without_client_is_refused(connected, monkeypatch):
    monkeypatch.setenv("TORQUE_WORKSPACE", str(connected))
    monkeypatch.setattr(c, "_consumed_approval", lambda *a: {"org_id_18": "00D000000000002AAA"})
    assert ctx().resolve_org() == c.EXIT_NOT_APPROVED


def test_revert_child_uses_the_parent_approval(connected, monkeypatch):
    monkeypatch.setenv(c.APPROVED_PARENT_ENV, "apr-000000000001")
    monkeypatch.setattr(c, "_consumed_approval", lambda *a: None)
    monkeypatch.setattr(c, "_parent_approval", lambda w, cl, ident, org: {"org_id_18": "00D000000000002AAA"}
                        if ident == "apr-000000000001" else None)
    assert ctx().resolve_org() == 0
    monkeypatch.setattr(c, "_parent_approval", lambda *a: None)
    assert ctx().resolve_org() == c.EXIT_NOT_APPROVED


def test_not_connected_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("TORQUE_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(c.org_detect, "resolve_org", lambda alias, **k: LIVE)
    monkeypatch.setattr(c, "_consumed_approval", lambda *a: pytest.fail("not connected"))
    assert ctx().resolve_org() == 0
