"""Core CLI and delegate contracts tested without installed clients or live orgs."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from torque import cli
from torque import workspace as ws


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "firm"
        ws.init_workspace(self.root, "Example")
        self.alpha = ws.add_client(self.root, "Alpha", "alpha-sandbox")
        self.beta = ws.add_client(self.root, "Beta", "beta-sandbox")
        self.env = patch.dict(os.environ)
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop("TORQUE_WORKSPACE", None)
        os.environ.pop("JSC_ROOT", None)

    def run_cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def context_args(self, client="Alpha"):
        return ["--workspace", str(self.root), "--client", client]

    def test_help_does_not_import_workflow_packages(self):
        with patch.object(cli.importlib, "import_module", side_effect=AssertionError("unexpected import")):
            code, output, error = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("workspace", output)
        self.assertIn("advisory", output)
        self.assertEqual(error, "")

    def test_end_to_end_session_context_and_handoff_keep_status_honest(self):
        code, _, _ = self.run_cli("session", "add", *self.context_args(), "--summary", "Prepared a field change")
        self.assertEqual(code, 0)
        code, output, _ = self.run_cli("context", *self.context_args(), "--json")
        data = json.loads(output)
        self.assertEqual(data["sessions"][0]["status"], "prepared")
        self.assertIs(data["sessions"][0]["independently_verified"], False)
        destination = self.alpha / "artifacts" / "handoff.md"
        code, _, _ = self.run_cli("handoff", *self.context_args(), "--output", str(destination))
        self.assertEqual(code, 0)
        self.assertIn("Prepared a field change", destination.read_text(encoding="utf-8"))
        self.assertIn("user-reported", destination.read_text(encoding="utf-8"))
        code, _, _ = self.run_cli("handoff", *self.context_args(), "--output", str(destination))
        self.assertEqual(code, 2)

    def test_session_show_does_not_read_another_client_entry(self):
        entry = ws.add_session(self.root, "Beta", "Secret beta work")
        code, output, error = self.run_cli("session", "show", entry["id"], *self.context_args(), "--json")
        self.assertEqual(code, 2)
        self.assertNotIn("Secret beta", output + error)

    def test_advisory_standalone_preserves_arguments_exit_and_ignores_legacy_root(self):
        seen = []
        os.environ["JSC_ROOT"] = "/old/employer/checkout"
        def delegate(argv):
            seen.append(argv)
            return 3
        module = types.SimpleNamespace(main=delegate)
        with patch.object(cli.importlib, "import_module", return_value=module):
            code, _, _ = self.run_cli("advisory", "impact", "--target-org", "dev", "--sobject", "Account")
        self.assertEqual(code, 3)
        self.assertEqual(seen, [["impact", "--target-org", "dev", "--sobject", "Account"]])

    def test_stateful_calls_scope_each_client_and_restore_environment_and_argv(self):
        observations = []
        os.environ["JSC_ROOT"] = "previous-root"
        os.environ["JSC_REVERT_DIR"] = "another-client-state"
        old_argv = sys.argv
        def delegate(argv):
            observations.append((argv[:], os.environ["TORQUE_WORKSPACE"], os.environ["JSC_ROOT"],
                                 os.environ.get("JSC_REVERT_DIR"), sys.argv[:]))
            return 7
        with patch.object(cli.importlib, "import_module", return_value=types.SimpleNamespace(main=delegate)):
            for client in ("Alpha", "Beta"):
                code, _, _ = self.run_cli("revert", "revert", "show", "--org", "explicit-org", *self.context_args(client))
                self.assertEqual(code, 7)
                self.assertNotIn("TORQUE_WORKSPACE", os.environ)
                self.assertEqual(os.environ["JSC_ROOT"], "previous-root")
                self.assertEqual(os.environ["JSC_REVERT_DIR"], "another-client-state")
                self.assertIs(sys.argv, old_argv)
        self.assertEqual(observations[0][1:4], (str(self.alpha), str(self.alpha), None))
        self.assertEqual(observations[1][1:4], (str(self.beta), str(self.beta), None))
        self.assertEqual(observations[0][0], ["revert", "show", "--org", "explicit-org"])

    def test_environment_restored_when_delegate_exits(self):
        def delegate(argv):
            raise SystemExit(4)
        old_argv = sys.argv
        with patch.object(cli.importlib, "import_module", return_value=types.SimpleNamespace(main=delegate)):
            with self.assertRaises(SystemExit) as caught:
                cli.main(["lesson", "show", *self.context_args()])
        self.assertEqual(caught.exception.code, 4)
        self.assertNotIn("TORQUE_WORKSPACE", os.environ)
        self.assertNotIn("JSC_ROOT", os.environ)
        self.assertIs(sys.argv, old_argv)

    def test_selected_client_adapter_files_follow_ported_package_contracts(self):
        config = self.alpha / "config"
        (config / "test-users.json").write_text("{}", encoding="utf-8")
        (config / "object-registry.yaml").write_text("objects: {}", encoding="utf-8")
        (config / "browser-flows").mkdir()
        (config / "ai-fixtures").mkdir()
        os.environ["TORQUE_TEST_USERS"] = "/stale/other-client.json"
        os.environ["TORQUE_AI_FIXTURES"] = "/stale/other-client-ai-fixtures"
        seen = []
        def delegate(argv):
            seen.append({key: os.environ.get(key) for key in
                         ("TORQUE_TEST_USERS", "TORQUE_BROWSER_REGISTRY", "TORQUE_BROWSER_FLOWS", "TORQUE_AI_FIXTURES")})
            return 0
        with patch.object(cli.importlib, "import_module", return_value=types.SimpleNamespace(main=delegate)):
            self.assertEqual(self.run_cli("browser", "suite-run", "--target-org", "dev", *self.context_args())[0], 0)
            self.assertEqual(self.run_cli("browser", "suite-run", "--target-org", "dev", *self.context_args("Beta"))[0], 0)
        self.assertEqual(seen[0]["TORQUE_TEST_USERS"], str(config / "test-users.json"))
        self.assertEqual(seen[0]["TORQUE_BROWSER_REGISTRY"], str(config / "object-registry.yaml"))
        self.assertEqual(seen[0]["TORQUE_BROWSER_FLOWS"], str(config / "browser-flows"))
        self.assertEqual(seen[0]["TORQUE_AI_FIXTURES"], str(config / "ai-fixtures"))
        self.assertTrue(all(value is None for value in seen[1].values()))
        self.assertEqual(os.environ["TORQUE_TEST_USERS"], "/stale/other-client.json")
        self.assertEqual(os.environ["TORQUE_AI_FIXTURES"], "/stale/other-client-ai-fixtures")

    def test_invalid_delegate_state_is_reported_and_context_is_restored(self):
        old_argv = sys.argv
        def delegate(argv):
            raise ValueError("State path escapes the selected client workspace")
        with patch.object(cli.importlib, "import_module", return_value=types.SimpleNamespace(main=delegate)):
            code, _, error = self.run_cli("lesson", "show", *self.context_args())
        self.assertEqual(code, 2)
        self.assertIn("State path escapes", error)
        self.assertNotIn("TORQUE_WORKSPACE", os.environ)
        self.assertNotIn("JSC_ROOT", os.environ)
        self.assertIs(sys.argv, old_argv)

    def test_parity_adapter_is_explicit_client_local_and_does_not_reuse_previous_scope(self):
        config = self.alpha / "config"
        (config / "parity.py").write_text("# synthetic fixture; never executed", encoding="utf-8")
        (config / "parity.json").write_text(json.dumps({"script": "parity.py", "baseline_org": "alpha-baseline"}), encoding="utf-8")
        os.environ["TORQUE_PARITY_SCRIPT"] = "/stale/other-client.py"
        os.environ["TORQUE_BASELINE_ORG"] = "stale-baseline"
        seen = []
        def delegate(argv):
            seen.append((os.environ.get("TORQUE_PARITY_SCRIPT"), os.environ.get("TORQUE_BASELINE_ORG")))
            return 0
        with patch.object(cli.importlib, "import_module", return_value=types.SimpleNamespace(main=delegate)):
            self.assertEqual(self.run_cli("qa", "run", "synthetic", "--org", "explicit", *self.context_args())[0], 0)
            self.assertEqual(self.run_cli("qa", "run", "synthetic", "--org", "explicit", *self.context_args("Beta"))[0], 0)
            (config / "parity.json").write_text(json.dumps({"script": "../context.md", "baseline_org": "alpha-baseline"}), encoding="utf-8")
            self.assertEqual(self.run_cli("qa", "run", "synthetic", "--org", "explicit", *self.context_args())[0], 2)
        self.assertEqual(seen, [(str(config / "parity.py"), "alpha-baseline"), (None, None)])
        self.assertEqual(os.environ["TORQUE_PARITY_SCRIPT"], "/stale/other-client.py")
        self.assertEqual(os.environ["TORQUE_BASELINE_ORG"], "stale-baseline")

    def test_no_argv_delegate_uses_scoped_sys_argv(self):
        seen = []
        def delegate():
            seen.append(sys.argv[:])
            return 6
        with patch.object(cli.importlib, "import_module", return_value=types.SimpleNamespace(main=delegate)):
            code, _, _ = self.run_cli("logs", "--log-file", "sample.log")
        self.assertEqual(code, 6)
        self.assertEqual(seen, [["torque logs", "--log-file", "sample.log"]])

    def test_missing_state_context_fails_before_import_or_org_work(self):
        with patch.object(cli.importlib, "import_module", side_effect=AssertionError("must not import")):
            code, _, error = self.run_cli("revert", "data", "update", "--target-org", "dev")
        self.assertEqual(code, 2)
        self.assertIn("selected client", error)

    def test_delegate_help_and_browser_sanitize_need_no_workspace(self):
        seen = []
        def delegate(argv):
            seen.append(argv)
            return 0
        with patch.object(cli.importlib, "import_module", return_value=types.SimpleNamespace(main=delegate)):
            self.assertEqual(self.run_cli("browser", "--help")[0], 0)
            self.assertEqual(self.run_cli("browser", "sanitize-replay", "recording.py")[0], 0)
        self.assertEqual(seen, [["--help"], ["sanitize-replay", "recording.py"]])

    def test_help_ignores_stale_context_but_literal_help_content_does_not_skip_client_selection(self):
        os.environ["TORQUE_WORKSPACE"] = "/nonexistent/old-workspace"
        with patch.object(cli.importlib, "import_module", return_value=types.SimpleNamespace(main=lambda argv: 0)):
            self.assertEqual(self.run_cli("qa", "--help")[0], 0)
        os.environ.pop("TORQUE_WORKSPACE")
        with patch.object(cli.importlib, "import_module", side_effect=AssertionError("must not import")):
            self.assertEqual(self.run_cli("lesson", "capture", "--", "--help")[0], 2)

    def test_missing_optional_dependency_does_not_break_core(self):
        missing = ModuleNotFoundError("No module named 'playwright'", name="playwright")
        with patch.object(cli.importlib, "import_module", side_effect=missing):
            code, _, error = self.run_cli("browser", "--help")
        self.assertEqual(code, 2)
        self.assertIn("playwright", error)
        self.assertEqual(self.run_cli("context", *self.context_args())[0], 0)

    def test_ai_regression_help_is_standalone_and_replay_scopes_private_prompt_state(self):
        observed = []
        def delegate(argv):
            observed.append((argv, os.environ.get("TORQUE_WORKSPACE")))
            return 0
        with patch.object(cli.importlib, "import_module", return_value=types.SimpleNamespace(main=delegate)):
            self.assertEqual(self.run_cli("ai-regression", "--help")[0], 0)
            self.assertEqual(self.run_cli("ai-regression", "replay", "fixture", *self.context_args())[0], 0)
            self.assertEqual(self.run_cli("ai-regression", "replay", "fixture")[0], 2)
        self.assertEqual(observed, [(["--help"], None), (["replay", "fixture"], str(self.alpha))])

    def test_meeting_output_explicit_or_selected_client(self):
        seen = []
        def delegate(argv):
            seen.append(argv)
            return 0
        with patch.object(cli.importlib, "import_module", return_value=types.SimpleNamespace(main=delegate)):
            self.assertEqual(self.run_cli("meeting", "--video", "demo.mp4")[0], 2)
            self.assertEqual(self.run_cli("meeting", "--video", "demo.mp4", "--output", "chosen")[0], 0)
            self.assertEqual(self.run_cli("meeting", "--video", "demo.mp4", *self.context_args())[0], 0)
        self.assertEqual(seen[0][-2:], ["--output", "chosen"])
        self.assertTrue(seen[1][-1].startswith(str(self.alpha / "artifacts" / "meetings")))

    def test_doctor_uses_local_presence_checks_only(self):
        with patch.object(cli.shutil, "which", return_value=None), patch.object(cli.importlib.util, "find_spec", return_value=None):
            code, output, _ = self.run_cli("doctor", *self.context_args(), "--json")
        self.assertEqual(code, 0)
        report = json.loads(output)
        self.assertIs(report["org_calls"], False)
        self.assertEqual(report["client"]["name"], "Alpha")

    def test_doctor_missing_optional_runtime_is_incomplete_only_when_requested(self):
        with patch.object(cli.shutil, "which", return_value=None), patch.object(cli.importlib.util, "find_spec", return_value=None):
            code, output, _ = self.run_cli("doctor", "--for", "browser", "--json")
        self.assertEqual(code, 3)
        data = json.loads(output)
        self.assertEqual(data["capabilities"]["browser"]["missing"], ["sf", "playwright"])
        self.assertFalse(data["capabilities"]["browser"]["live_verified"])

    def test_doctor_reports_tracked_private_files_without_changing_git(self):
        from subprocess import CompletedProcess
        with patch.object(cli.subprocess, "run", return_value=CompletedProcess([], 0, "clients/alpha/client.json\n", "")) as process:
            code, output, _ = self.run_cli("doctor", *self.context_args(), "--json")
        self.assertEqual(code, 0)
        self.assertEqual(process.call_args.args[0][:2], ["git", "ls-files"])
        data = json.loads(output)
        self.assertEqual(data["git_tracking"]["tracked_private_paths"], ["clients/alpha/client.json"])
        self.assertTrue(any("already tracked" in line for line in data["next_actions"]))

    def test_change_cli_connects_reported_checks_to_context_and_json_handoff(self):
        code, output, _ = self.run_cli("change", "create", *self.context_args(), "--title", "Contact preference",
                                      "--outcome", "Staff can find the preference", "--criterion", "Value persists", "--json")
        self.assertEqual(code, 0)
        identifier = json.loads(output)["id"]
        self.assertEqual(self.run_cli("change", "note", identifier, *self.context_args(), "--kind", "decision", "--text", "Keep optional")[0], 0)
        self.assertEqual(self.run_cli("change", "check", identifier, *self.context_args(), "--criterion", "AC1", "--result", "pass", "--summary", "Operator observed saved value")[0], 0)
        context = json.loads(self.run_cli("context", *self.context_args(), "--json")[1])
        self.assertEqual(context["changes"][0]["id"], identifier)
        self.assertIn("Keep optional", self.run_cli("handoff", *self.context_args())[1])
        target = self.alpha / "artifacts" / "change.json"
        code, output, _ = self.run_cli("change", "handoff", identifier, *self.context_args(), "--json", "--output", str(target))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output), json.loads(target.read_text(encoding="utf-8")))
        self.assertFalse(json.loads(output)["assessment"]["business_acceptance_independently_verified"])
        self.assertEqual(self.run_cli("change", "show", identifier, *self.context_args("Beta"))[0], 2)

    def test_demo_and_upgrade_cli_are_ready_without_any_org(self):
        destination = Path(self.temp.name) / "demo"
        code, output, _ = self.run_cli("demo", str(destination), "--json")
        self.assertEqual(code, 0)
        result = json.loads(output)
        self.assertFalse(result["org_calls"])
        self.assertIn("change_id", result)
        code, output, _ = self.run_cli("workspace", "upgrade", str(destination), "--check", "--json")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output)["check"])
        code, output, _ = self.run_cli("client", "list", "--workspace", str(destination), "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)[0]["org"], None)

    def test_workflow_show_uses_catalogue_not_arbitrary_paths(self):
        catalogue = json.dumps([{"name": "diagnose", "description": "Investigate", "kind": "guided"}])
        def text(path):
            return {"catalogue.json": catalogue, "diagnose.md": "# Diagnose\nRecipe"}.get(path)
        with patch.object(cli, "_data_text", side_effect=text):
            code, output, _ = self.run_cli("workflows", "show", "diagnose")
            self.assertEqual(code, 0)
            self.assertIn("Recipe", output)
            self.assertEqual(self.run_cli("workflows", "show", "../secret")[0], 2)


if __name__ == "__main__":
    unittest.main()


def test_public_delivery_routes_preserve_native_target_and_private_scope(tmp_path):
    from unittest.mock import patch
    seen = []
    with patch.object(cli, '_dispatch', side_effect=lambda route, args, display=None: seen.append((route, args)) or 0):
        assert cli.main(['deploy','--target-org','explicit','--workspace','private','--client','alpha']) == 0
        assert cli.main(['data','update','--target-org','explicit']) == 0
        assert cli.main(['recover','run','snapshot','--org','explicit']) == 0
    assert seen[0] == ('revert',['deploy','--target-org','explicit','--workspace','private','--client','alpha'])
    assert seen[1] == ('revert',['data','update','--target-org','explicit'])
    assert seen[2] == ('revert',['revert','exec','snapshot','--org','explicit'])
