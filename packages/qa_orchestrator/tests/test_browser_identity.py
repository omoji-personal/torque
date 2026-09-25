"""qa's browser cells run Torque's browser route with --json and report each cell's
identity (org, admin before Login As, the user after it, the admin restored), never
the session URL."""
import json
import sys
from types import SimpleNamespace

import pytest

from jsc_qa import dispatcher, report

ORG = "00D000000000003AAA"
ADMIN = "005000000000001AAA"
USER = "005000000000002AAA"
SID = "00D000000000003!AQ8AQsynthetic.token"
SECRET = f"https://x.my.salesforce.com/secur/frontdoor.jsp?sid={SID}"


def cell(profile, user_after=None, restored=None):
    return {"flow": "smoke_login", "profile": profile, "status": "PASS", "error": f"went via {SECRET}",
            "steps": [], "side_effects": {"url": SECRET},
            "identity": {"org_id_18": ORG, "org_verified_by": "username", "admin_before": ADMIN,
                         "admin_username": "admin@acme-dev.example", "user_after_login_as": user_after,
                         "admin_restored": restored, "restored": bool(restored),
                         "status": "MATCHED" if user_after else "OBSERVED"}}


@pytest.fixture
def browser_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("TORQUE_WORKSPACE", str(tmp_path))
    calls = []

    def fake_run(argv, **kwargs):
        assert argv[:3] == [sys.executable, "-m", "jsc_browser_tests.cli"]
        calls.append(argv)
        cells = [cell("admin")] + ([cell("standard", USER, ADMIN)] if argv[3] == "multiprofile" else [])
        # Unredacted on purpose: the dispatcher redacts what it keeps as well.
        return SimpleNamespace(returncode=0, stdout=json.dumps(cells), stderr=f"debug {SECRET}")
    monkeypatch.setattr(dispatcher.subprocess, "run", fake_run)
    return calls


@pytest.mark.parametrize("surface,command", [("Funct-Pl", "browser"), ("Multi-Prof", "multiprofile")])
def test_qa_browser_cells_report_identity_and_no_session_url(browser_cli, surface, command):
    result = dispatcher.dispatch_surface(surface, "acme-dev", "smoke_login check")
    assert browser_cli and browser_cli[0][3] == command and "--json" in browser_cli[0]
    identities = result.metadata["identity"]
    assert identities[0]["profile"] == "admin" and identities[0]["org_id_18"] == ORG
    assert identities[0]["admin_before"] == ADMIN
    if command == "multiprofile":
        assert identities[1]["user_after_login_as"] == USER and identities[1]["admin_restored"] == ADMIN
        assert identities[1]["restored"] is True
    text = report.format_report([], "acme-dev", False, [result]) + json.dumps(result.metadata) + result.raw_output
    assert f"org {ORG}" in text and ADMIN in text
    if command == "multiprofile":
        assert f"user after Login As {USER}" in text and f"admin restored {ADMIN}" in text
    for needle in ("frontdoor.jsp", "sid=", SID, "secur/"):
        assert needle not in text


def test_unreadable_browser_output_reports_no_identity(monkeypatch, tmp_path):
    monkeypatch.setenv("TORQUE_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(dispatcher.subprocess, "run",
                        lambda argv, **kwargs: SimpleNamespace(returncode=1, stdout=f"crash {SECRET}", stderr=""))
    result = dispatcher.dispatch_surface("Funct-Pl", "acme-dev", "smoke_login check")
    assert result.status == "FAIL" and result.metadata["identity"] == []
    assert "identity: not reported" in result.detail
    assert SID not in result.raw_output and "frontdoor.jsp" not in result.raw_output
