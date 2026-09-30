"""Evidence and exports never cross into another client or initiative."""
from pathlib import Path

from torque import workspace as ws


def test_foreign_engagement(tmp_path):
    root = tmp_path / "w"
    own = root / "clients" / "alpha"
    assert ws.foreign_engagement(own, root / "clients" / "beta" / "x.txt")
    assert ws.foreign_engagement(own, root / "initiatives" / "plan" / "x.txt")
    assert not ws.foreign_engagement(own, own / "artifacts" / "x.txt")
    assert not ws.foreign_engagement(own, root / "profile.md")
    assert not ws.foreign_engagement(own, tmp_path / "outside.txt")
    initiative = root / "initiatives" / "plan"
    assert ws.foreign_engagement(initiative, own / "x.txt")
    assert not ws.foreign_engagement(initiative, initiative / "context.md")


def test_client_session_evidence_from_an_initiative_is_refused(tmp_path):
    root = ws.init_workspace(tmp_path / "firm", "Synthetic firm")
    ws.add_client(root, "Alpha")
    other = root / "initiatives" / "plan"
    other.mkdir(parents=True)
    evidence = other / "notes.txt"
    evidence.write_text("internal\n")
    try:
        ws.add_session(root, "Alpha", "work", "prepared", evidence)
    except ws.WorkspaceError as exc:
        assert "different client" in str(exc)
    else:
        raise AssertionError("evidence from an initiative was accepted")


def test_foreign_engagement_ignores_case_on_a_case_insensitive_filesystem(tmp_path):
    import os
    from torque import engagements as eng
    root = Path(os.path.realpath(ws.init_workspace(tmp_path / "firm", "Synthetic firm")))
    ws.add_client(root, "Alpha")
    ws.add_client(root, "Beta")
    eng.add_initiative(root, "Plan")
    own = root / "clients" / "alpha"
    variants = [root / "CLIENTS" / "beta" / "x.txt", root / "Initiatives" / "plan" / "x.txt"]
    for path in variants:
        if not (path.parent.exists() and os.path.samefile(path.parent, root / path.parts[-3].lower() / path.parts[-2])):
            continue  # case-sensitive filesystem: the variant is a different folder
        assert ws.foreign_engagement(own, path), path
    mixed = root / "Clients" / "Alpha" / "x.txt"
    if mixed.parent.exists() and os.path.samefile(mixed.parent, own):
        assert not ws.foreign_engagement(own, mixed)
