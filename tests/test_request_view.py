import hashlib
import json
import os

import pytest

from delegated_helpers import ORGS, delegated_workspace, flow_request
from torque import approval, changes, cli, workspace as ws

pytestmark = pytest.mark.skipif(not hasattr(os, "getuid"), reason="tier 2 is POSIX only")
SCOPE = ("operation", "org_alias", "org_id_18", "org_kind", "components", "cwd")


def test_spelling_wrappers_and_non_scope_flags_do_not_change_the_fields(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    spellings = (None,
                 ["sf", "project", "deploy", "start", "-m", "Flow:Case_Escalation", "-o", "acme-dev", "--wait", "10"],
                 ["torque", "deploy", "--metadata", "Flow:Case_Escalation", "--target-org", "acme-dev"])
    views = [approval.request_view(root, "Acme", flow_request(root, argv)["id"], resolve=ORGS.get)
             for argv in spellings]
    assert [{k: v[k] for k in SCOPE} for v in views] == [{k: views[0][k] for k in SCOPE}] * 3
    assert views[0]["operation"] == "deploy" and views[0]["components"] == ["Flow:Case_Escalation"]
    assert views[0]["org_id_18"] == "00D000000000003AAA" and views[0]["org_kind"] == "developer"
    assert len({v["payload"]["digest"] for v in views}) == 1
    content = hashlib.sha256(b"<Flow>v2</Flow>").hexdigest()
    assert views[0]["payload"]["files"] == [
        {"path": "force-app/main/default/flows/Case_Escalation.flow-meta.xml", "sha256": content}]


def test_request_sha256_is_over_the_exact_file(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    path = root / "clients" / "acme" / "approvals" / "requests" / f"{req['id']}.json"
    view = approval.request_view(root, "Acme", req["id"], resolve=ORGS.get)
    assert view["request_sha256"] == "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    assert view["expires_at"] > view["created_at"]
    assert any("acme-dev" in line for line in view["screen"])


def test_record_writes_normalize_to_object_and_id(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    long = ["sf", "data", "update", "record", "--sobject", "Contact", "--record-id", "003000000000001AAA",
            "--values", "Title=Steward", "--target-org", "acme-dev"]
    short = ["sf", "data", "update", "record", "-s", "Contact", "-i", "003000000000001AAA",
             "-v", "Title=Steward", "-o", "acme-dev"]
    views = [approval.request_view(root, "Acme", flow_request(root, a)["id"], resolve=ORGS.get) for a in (long, short)]
    assert views[0]["operation"] == views[1]["operation"] == "data update"
    assert views[0]["components"] == views[1]["components"] == ["Record:Contact:003000000000001AAA"]


def test_record_upserts_normalize_to_object_and_external_field(tmp_path, monkeypatch):
    """F20: an upsert names an object plus external ID field, not a record ID; both
    the long and short flag spellings of --sobject/--external-id must agree."""
    root = delegated_workspace(tmp_path, monkeypatch)
    long = ["sf", "data", "upsert", "record", "--sobject", "Contact", "--external-id", "Email_Ext_Id__c",
            "--values", "Email_Ext_Id__c=steward@acme.example Title=Steward", "--target-org", "acme-dev"]
    short = ["sf", "data", "upsert", "record", "-s", "Contact", "-i", "Email_Ext_Id__c",
             "-v", "Email_Ext_Id__c=steward@acme.example Title=Steward", "-o", "acme-dev"]
    views = [approval.request_view(root, "Acme", flow_request(root, a)["id"], resolve=ORGS.get) for a in (long, short)]
    assert views[0]["operation"] == views[1]["operation"] == "data upsert"
    assert views[0]["components"] == views[1]["components"] == ["Record:Contact:external:Email_Ext_Id__c"]


def test_browser_request_view(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    cid = changes.create_change(root, "Acme", "Check", "Visit saves", [], "acme-dev")["id"]
    req = approval.create_request(root, "Acme", cid, "acme-dev", browser_minutes=20,
                                  purpose="Login As check of the visit form", resolve=ORGS.get)
    view = approval.request_view(root, "Acme", req["id"], resolve=ORGS.get)
    assert (view["operation"], view["components"], view["payload"]["digest"]) == ("browser window", [], None)


def test_cli_show_json(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    monkeypatch.setattr(approval, "_resolver", lambda resolve: resolve or ORGS.get)
    assert cli.main(["approval", "show", req["id"], "--workspace", str(root), "--client", "Acme", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["schema"] == approval.VIEW_SCHEMA


# Fix round 1, item 2: every screen line is escaped once (control bytes and a
# bidirectional-override character shown as escapes), in the view (both text and
# --json) and in the human grant screen, which must stay byte-identical to a15.


def test_view_screen_escapes_control_and_bidi_characters_once(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    argv = ["sf", "data", "update", "record", "-s", "Account", "-i", "001000000000001AAA",
            "-v", "Name=A\x1b[2K‮Evil\rName=B", "-o", "acme-dev"]
    req = flow_request(root, argv)
    view = approval.request_view(root, "Acme", req["id"], resolve=ORGS.get)
    joined = "\n".join(view["screen"])
    assert "\x1b" not in joined and "‮" not in joined and "\r" not in joined
    assert "\\x1b" in joined and "\\u202e" in joined
    assert "\\\\x1b" not in joined and "\\\\u202e" not in joined  # escaped exactly once

    monkeypatch.setattr(approval, "_resolver", lambda resolve: resolve or ORGS.get)
    assert cli.main(["approval", "show", req["id"], "--workspace", str(root), "--client", "Acme"]) == 0
    text_out = capsys.readouterr().out
    assert "\x1b" not in text_out and "‮" not in text_out
    assert "\\x1b" in text_out and "\\u202e" in text_out

    assert cli.main(["approval", "show", req["id"], "--workspace", str(root), "--client", "Acme", "--json"]) == 0
    json_text = capsys.readouterr().out
    assert "\x1b" not in json_text and "‮" not in json_text
    data = json.loads(json_text)
    assert data["screen"] == view["screen"]


def test_grant_screen_stays_byte_identical_to_a15_with_the_same_control_characters(tmp_path, monkeypatch):
    """The human grant screen escapes each line once, at write time (out.write in
    grant()), exactly as a15 did; the new shared _screen_lines helper (feeding
    both the view and grant()) must not change that output."""
    import io
    from torque.presence import Presence
    # R45: an owner grant needs a workspace whose approver is a person.
    root = delegated_workspace(tmp_path, monkeypatch, kind="human")
    argv = ["sf", "data", "update", "record", "-s", "Account", "-i", "001000000000001AAA",
            "-v", "Name=A\x1b[2K‮Evil\rName=B", "-o", "acme-dev"]
    req = flow_request(root, argv)
    out = io.StringIO()
    approval.grant(root, "Acme", req["id"], presence=lambda: Presence(True, ""), confirm=lambda: True, out=out,
                   resolve=ORGS.get)
    screen = out.getvalue()
    assert not any(ord(c) < 32 and c != "\n" for c in screen), repr(screen)
    assert "‮" not in screen
    assert "\\x1b" in screen and "\\u202e" in screen


# Fix round 1, item 3: payload_digest and payload_listing read the same entries
# (via the shared _payload_entries), so a view's files and its digest agree, and
# a symlink / an unreadable file hash exactly as a15's payload_digest did.


def test_view_payload_files_reconcile_with_the_digest(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    view = approval.request_view(root, "Acme", req["id"], resolve=ORGS.get)
    lines = sorted(f"{f['path']}\0{f['sha256']}\n" for f in view["payload"]["files"])
    recomputed = "sha256:" + hashlib.sha256("".join(lines).encode()).hexdigest()
    assert recomputed == view["payload"]["digest"]
    assert len(view["payload"]["files"]) == view["payload"]["count"]


def test_payload_digest_and_listing_share_one_read_symlink_and_unreadable_parity(tmp_path):
    """V2 I3: an unreadable payload file is refused by both readers alike (never
    hashed as a constant); readable files and links keep one shared read."""
    if os.name == "nt":
        pytest.skip("chmod(0o000) and symlinks are not reliably testable on Windows")
    folder = tmp_path / "payload"
    folder.mkdir()
    (folder / "plain.txt").write_bytes(b"plain-bytes")
    target = folder / "target.txt"
    target.write_bytes(b"target-bytes")
    (folder / "link.txt").symlink_to(target)
    argv = ["sf", "project", "deploy", "start", "--source-dir", str(folder), "--target-org", "acme-dev"]
    digest, count = approval.payload_digest(argv, tmp_path, capped=False)
    listing = approval.payload_listing(argv, tmp_path)
    assert count == len(listing) == 3
    expected_lines = sorted(f"{f['path']}\0{f['sha256']}\n" for f in listing)
    assert digest == "sha256:" + hashlib.sha256("".join(expected_lines).encode()).hexdigest()
    by_path = {f["path"]: f["sha256"] for f in listing}
    assert by_path["payload/plain.txt"] == hashlib.sha256(b"plain-bytes").hexdigest()
    assert by_path["payload/target.txt"] == hashlib.sha256(b"target-bytes").hexdigest()
    assert by_path["payload/link.txt"] == hashlib.sha256(
        ("link:" + str(target) + "\0").encode() + b"target-bytes").hexdigest()
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return
    locked = folder / "locked.txt"
    locked.write_bytes(b"locked-bytes")
    locked.chmod(0o000)
    try:
        with pytest.raises(ws.WorkspaceError, match="cannot be read"):
            approval.payload_digest(argv, tmp_path, capped=False)
        with pytest.raises(ws.WorkspaceError, match="cannot be read"):
            approval.payload_listing(argv, tmp_path)
    finally:
        locked.chmod(0o600)


# Fix round 1, item 4: a tampered or malformed request surfaces as WorkspaceError
# (validated once, in load_request_hashed), and a benign tamper still changes the
# request's hash (tamper-evidence even where a field is not itself validated).


def test_request_view_refuses_a_missing_request(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    with pytest.raises(ws.WorkspaceError, match="no readable request"):
        approval.request_view(root, "Acme", "req-000000000000", resolve=ORGS.get)


def test_request_view_refuses_a_malformed_request_file(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    path = root / "clients" / "acme" / "approvals" / "requests" / f"{req['id']}.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(ws.WorkspaceError):
        approval.request_view(root, "Acme", req["id"], resolve=ORGS.get)


def test_request_view_refuses_a_tampered_created_at(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    path = root / "clients" / "acme" / "approvals" / "requests" / f"{req['id']}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["created_at"] = "not-a-timestamp"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ws.WorkspaceError):
        approval.request_view(root, "Acme", req["id"], resolve=ORGS.get)
    data["created_at"] = "2026-09-30T12:00:00"  # no time zone: _epoch rejects it too
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ws.WorkspaceError):
        approval.request_view(root, "Acme", req["id"], resolve=ORGS.get)


@pytest.mark.parametrize("field", ["org_alias", "kind"])
def test_request_view_refuses_a_request_missing_org_alias_or_kind(tmp_path, monkeypatch, field):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    path = root / "clients" / "acme" / "approvals" / "requests" / f"{req['id']}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    del data[field]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ws.WorkspaceError):
        approval.request_view(root, "Acme", req["id"], resolve=ORGS.get)


def test_request_view_hash_changes_when_the_request_is_tampered(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    path = root / "clients" / "acme" / "approvals" / "requests" / f"{req['id']}.json"
    original = approval.request_view(root, "Acme", req["id"], resolve=ORGS.get)["request_sha256"]
    data = json.loads(path.read_text(encoding="utf-8"))
    data["purpose"] = "tampered after the fact"
    path.write_text(json.dumps(data), encoding="utf-8")
    tampered = approval.request_view(root, "Acme", req["id"], resolve=ORGS.get)["request_sha256"]
    assert tampered != original
