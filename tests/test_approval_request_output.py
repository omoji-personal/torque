"""What `torque approval request` and `grant` print and do for the consultant (audit item
N15): the grant command a request prints names the workspace by its full path, so it runs
from the consultant's own terminal in any folder; a request resolves its org once; and a
tier 2 grant from the wrong account stops before the live org read, the review screen and
the typed code."""
from collections import namedtuple
import io
import os
from pathlib import Path
import re

import pytest

from torque import approval, changes, cli, consent, workspace as ws
from torque.presence import Presence

YES = lambda: Presence(True, "")
Org = namedtuple("Org", "org_id_18 detected_org_type is_production instance_url")
ORG = Org("00D000000000001AAA", "sandbox", False, "https://acme--sbx.sandbox.my.salesforce.com")
WINDOW = ["--browser", "--minutes", "20", "--purpose", "Add Tier to the Case layout"]


@pytest.fixture
def connected(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    root = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(root, "Acme")
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    letter = tmp_path / "a.pdf"
    letter.write_bytes(b"agreement")
    consent.record_consent(root, "Acme", "2026-09-30", letter, ["metadata"], ["acme-sbx"], ["Contact"],
                           presence=YES, resolve={"acme-sbx": ORG}.get)
    consent.sign_off(root, "Acme", "Reviewer", presence=YES)
    return Path(os.path.realpath(root))


def test_a_request_prints_a_grant_command_that_works_from_another_folder(connected, tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr("jsc_revert.org_detect.resolve_org", lambda alias, **k: calls.append(alias) or ORG)
    cid = changes.create_change(connected, "Acme", "Layout", "Cases show Tier", [], "acme-sbx")["id"]
    monkeypatch.chdir(connected)
    assert cli.main(["approval", "request", "--workspace", ".", "--client", "Acme", "--change", cid,
                     "--org", "acme-sbx", *WINDOW]) == 0
    # One live resolve: the request reuses the answer the CLI layer checked first.
    assert calls == ["acme-sbx"]
    out = capsys.readouterr().out
    request_id = re.search(r"Request (req-[0-9a-f]{12}) recorded", out).group(1)
    argv = ["torque", "approval", "grant", request_id, "--workspace", str(connected), "--client", "Acme"]
    assert "  " + ws.command_text(argv) + "\n" in out and "--workspace ." not in out
    cli.build_parser().parse_args(argv[1:])
    # The consultant's terminal is elsewhere; the printed workspace still names this one.
    monkeypatch.chdir(tmp_path)
    granted = approval.grant(argv[5], "Acme", request_id, presence=YES, confirm=lambda: True, out=io.StringIO(),
                             resolve={"acme-sbx": ORG}.get)
    assert granted["request_id"] == request_id


def test_a_tier_2_grant_from_another_account_stops_before_the_review(connected, monkeypatch):
    cid = changes.create_change(connected, "Acme", "Layout", "Cases show Tier", [], "acme-sbx")["id"]
    req = approval.create_request(connected, "Acme", cid, "acme-sbx", browser_minutes=20,
                                  purpose="Add Tier to the Case layout", resolve={"acme-sbx": ORG}.get)
    # Tier 2 exists on macOS and Linux only; here only the account check matters.
    monkeypatch.setattr(ws, "_owner_uid_supported", lambda: True)
    other = os.getuid() + 1 if hasattr(os, "getuid") else 4242
    ws.set_ai_access(connected, "connected", approval="required", verify="owner-uid", approver_uid=other,
                     presence=YES)
    screen, asked, resolved = io.StringIO(), [], []
    with pytest.raises(ws.WorkspaceError, match="grant from that account"):
        approval.grant(connected, "Acme", req["id"], presence=YES, confirm=lambda: asked.append(1) or True,
                       out=screen, resolve=lambda alias: resolved.append(alias) or ORG)
    assert screen.getvalue() == "" and asked == [] and resolved == []
