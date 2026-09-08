"""Synthetic client isolation and honest journal behavior; no Salesforce processes."""
import json
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from torque import workspace as ws


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "firm"
        ws.init_workspace(self.root, "Example firm", "solution-lead")
        self.alpha = ws.add_client(self.root, "Alpha Client", "alpha-sandbox")
        self.beta = ws.add_client(self.root, "Beta", "beta-sandbox")

    def test_init_creates_private_conversational_workspace_without_overwriting(self):
        self.assertTrue((self.root / "AGENTS.md").is_file())
        self.assertTrue((self.root / "CLAUDE.md").is_file())
        self.assertIn("torque workflows", (self.root / "AGENTS.md").read_text())
        self.assertIn("solution-lead", (self.root / "profile.md").read_text())
        self.assertIn("Salesforce Solution Lead", (self.root / "profile.md").read_text())
        self.assertIn("discovery", json.loads((self.root / "workspace.json").read_text())["delivery_focus"])
        original = (self.root / "workspace.json").read_bytes()
        with self.assertRaises(ws.WorkspaceError):
            ws.init_workspace(self.root, "Other")
        self.assertEqual(original, (self.root / "workspace.json").read_bytes())
        self.assertEqual((self.root / "workspace.json").stat().st_mode & 0o777, 0o600)

    def test_rejects_source_checkout_without_creating_files(self):
        path = Path(self.temp.name) / "source" / "private"
        with patch.object(ws, "_source_checkout", return_value=path.parent):
            with self.assertRaises(ws.WorkspaceError):
                ws.init_workspace(path, "Private")
        self.assertFalse(path.exists())

    def test_packaged_workflows_are_copied_without_overwriting_existing_local_commands(self):
        package = Path(self.temp.name) / "package"
        command = package / "data" / "commands" / "diagnose.md"
        command.parent.mkdir(parents=True)
        command.write_text("Packaged diagnostic recipe")
        skill = package / "data" / "skills" / "context" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("Packaged context skill")
        target = Path(self.temp.name) / "another-firm"
        local = target / ".claude" / "commands" / "diagnose.md"
        local.parent.mkdir(parents=True)
        local.write_text("Local edited recipe")
        with patch.object(ws.resources, "files", return_value=package):
            ws.init_workspace(target, "Another")
        self.assertEqual(local.read_text(), "Local edited recipe")
        self.assertEqual((target / ".claude/skills/context/SKILL.md").read_text(), "Packaged context skill")
        self.assertEqual((target / ".agents/skills/context/SKILL.md").read_text(), "Packaged context skill")

    def test_context_reads_only_selected_client_notes_and_recent_journal(self):
        (self.alpha / "context" / "decision.md").write_text("Alpha decision")
        (self.beta / "context.md").write_text("Private beta detail")
        ws.add_session(self.root, "Alpha Client", "Alpha work")
        ws.add_session(self.root, "Beta", "Unrelated beta work")
        value = ws.get_context(self.root, "alpha-client")
        text = json.dumps(value)
        self.assertIn("Alpha decision", text)
        self.assertIn("Alpha work", text)
        self.assertNotIn("beta", text.lower())
        self.assertEqual(value["client"]["org"], "alpha-sandbox")

    def test_slug_safety_and_collision_do_not_touch_another_client(self):
        for name in ("", "../beta", "a/b", "a\\b", "...", "!!!"):
            with self.subTest(name=name), self.assertRaises(ws.WorkspaceError):
                ws.add_client(self.root, name)
        with self.assertRaises(ws.WorkspaceError):
            ws.add_client(self.root, "ALPHA CLIENT")
        self.assertTrue((self.alpha / "client.json").is_file())
        with self.assertRaises(ws.WorkspaceError):
            ws.load_client(self.root, "missing")

    def test_symlinks_cannot_pull_other_client_notes_or_sessions(self):
        (self.alpha / "context.md").unlink()
        (self.alpha / "context.md").symlink_to(self.beta / "context.md")
        with self.assertRaises(ws.WorkspaceError):
            ws.get_context(self.root, "alpha-client")
        (self.alpha / "context.md").unlink()
        (self.alpha / "sessions").rmdir()
        (self.alpha / "sessions").symlink_to(self.beta / "sessions", target_is_directory=True)
        with self.assertRaises(ws.WorkspaceError):
            ws.add_session(self.root, "alpha-client", "must not write to Beta")
        self.assertEqual(list((self.beta / "sessions").iterdir()), [])

    def test_reported_verified_is_not_independent_verification(self):
        evidence = self.alpha / "artifacts" / "check.txt"
        evidence.write_text("Synthetic observation")
        entry = ws.add_session(self.root, "alpha-client", "Owner reports checked", "verified", evidence)
        self.assertEqual(entry["status"], "verified")
        self.assertEqual(entry["status_basis"], "user_reported")
        self.assertIs(entry["independently_verified"], False)
        self.assertEqual(len(entry["evidence"]["sha256"]), 64)
        handoff = ws.render_handoff(self.root, "alpha-client")
        self.assertIn("did not independently verify", handoff)
        self.assertIn("user-reported", handoff)
        self.assertNotIn("Synthetic observation", handoff)

    def test_missing_or_other_client_evidence_creates_no_entry(self):
        for evidence in (self.alpha / "missing.txt", self.beta / "context.md"):
            with self.subTest(evidence=evidence), self.assertRaises(ws.WorkspaceError):
                ws.add_session(self.root, "alpha-client", "bad evidence", evidence=evidence)
        self.assertEqual(ws.list_sessions(self.root, "alpha-client"), [])

    def test_concurrent_journal_entries_are_complete_unique_and_private(self):
        def record(i):
            return ws.add_session(self.root, "alpha-client", f"Synthetic work {i}")
        with ThreadPoolExecutor(max_workers=4) as pool:
            written = list(pool.map(record, range(16)))
        entries = ws.list_sessions(self.root, "alpha-client", limit=None)
        self.assertEqual(len({e["id"] for e in written}), 16)
        self.assertEqual(len(entries), 16)
        for path in (self.alpha / "sessions").glob("*.json"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text())["id"], path.stem)
        self.assertEqual(list((self.alpha / "sessions").glob(".torque-*")), [])

    def test_atomic_publish_never_overwrites_existing_evidence(self):
        path = self.alpha / "artifacts" / "handoff.md"
        ws.atomic_write_new(path, "original")
        with self.assertRaises(ws.WorkspaceError):
            ws.atomic_write_new(path, "replacement")
        self.assertEqual(path.read_text(), "original")
        self.assertEqual(list(path.parent.glob(".torque-*")), [])

    def test_tampered_wrong_client_journal_is_rejected(self):
        entry = ws.add_session(self.root, "alpha-client", "Work")
        path = self.alpha / "sessions" / f"{entry['id']}.json"
        entry["client"] = "beta"
        path.write_text(json.dumps(entry))
        with self.assertRaises(ws.WorkspaceError):
            ws.list_sessions(self.root, "alpha-client")

    def test_empty_handoff_does_not_invent_progress(self):
        text = ws.render_handoff(self.root, "alpha-client")
        self.assertIn("No session entries", text)
        self.assertNotIn("completed", text.lower())


if __name__ == "__main__":
    unittest.main()


def test_existing_gitignore_does_not_leave_new_private_paths_stageable(tmp_path):
    import subprocess
    root = tmp_path / 'existing'
    root.mkdir()
    (root / '.gitignore').write_text('node_modules/\n')
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    ws.init_workspace(root, 'Private firm')
    ws.add_client(root, 'Alpha')
    rules = (root / '.gitignore').read_text()
    assert 'node_modules/' in rules
    checked = subprocess.run(['git','check-ignore','workspace.json','profile.md','clients/alpha/context.md'],
                             cwd=root, capture_output=True, text=True, check=True)
    assert set(checked.stdout.splitlines()) == {'workspace.json','profile.md','clients/alpha/context.md'}
