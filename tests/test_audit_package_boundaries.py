"""Client-local parity execution and package maintenance entry checks."""
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from torque import cli, workspace as ws


@pytest.fixture
def root(tmp_path, monkeypatch):
    for name in ("TORQUE_WORKSPACE", "JSC_ROOT", "TORQUE_PARITY_SCRIPT", "TORQUE_BASELINE_ORG"):
        monkeypatch.delenv(name, raising=False)
    root = ws.init_workspace(tmp_path / "workspace", "Synthetic workspace")
    ws.add_client(root, "Alpha")
    ws.add_client(root, "Beta")
    return root


@pytest.mark.parametrize("script", [r"..\..\beta\config\parity.py", r"nested\..\parity.py",
                                    r"C:\outside\parity.py", "C:parity.py", r"\parity.py",
                                    r"\\host\share\parity.py", "../parity.py", "/parity.py"])
def test_parity_rejects_portable_traversal_and_drive_paths_before_lookup(root, script):
    client = root / "clients" / "alpha"
    (client / "config" / "parity.json").write_text(
        json.dumps({"script": script, "baseline_org": "alpha-baseline"}), encoding="utf-8")
    with pytest.raises(ws.WorkspaceError, match="relative file under this client's config"):
        with cli.delegated_context(client, "qa", []):
            pytest.fail("invalid parity path reached the delegate")


def test_windows_parity_traversal_is_refused_even_when_the_target_exists(root):
    client = root / "clients" / "alpha"
    script = r"..\..\beta\config\parity.py"
    foreign = root / "clients" / "beta" / "config" / "parity.py"
    foreign.write_text("# synthetic foreign adapter", encoding="utf-8")
    if os.name != "nt":
        # On POSIX this spelling names one file, allowing the old validator to
        # admit it there too. On Windows it resolves to the foreign adapter.
        (client / "config" / script).write_text("# synthetic adapter", encoding="utf-8")
    (client / "config" / "parity.json").write_text(
        json.dumps({"script": script, "baseline_org": "alpha-baseline"}), encoding="utf-8")
    with pytest.raises(ws.WorkspaceError, match="relative file under this client's config"):
        with cli.delegated_context(client, "qa", []):
            pytest.fail("Windows traversal reached the delegate")


def test_parity_rejects_symlink_outside_config_even_within_client(root):
    client = root / "clients" / "alpha"
    target = client / "parity.py"
    target.write_text("# outside config", encoding="utf-8")
    try:
        (client / "config" / "parity.py").symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable")
    (client / "config" / "parity.json").write_text(
        json.dumps({"script": "parity.py", "baseline_org": "alpha-baseline"}), encoding="utf-8")
    with pytest.raises(ws.WorkspaceError):
        with cli.delegated_context(client, "qa", []):
            pytest.fail("escaped parity adapter reached the delegate")


@pytest.mark.parametrize("location", ["../beta/config/parity.py", "parity.py"])
def test_parity_dispatch_rechecks_selected_client_config(root, monkeypatch, location):
    from jsc_qa import dispatcher
    client = root / "clients" / "alpha"
    target = (client / location).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# must not run", encoding="utf-8")
    monkeypatch.setenv("TORQUE_WORKSPACE", str(client))
    monkeypatch.setenv("TORQUE_PARITY_SCRIPT", str(target))
    monkeypatch.setenv("TORQUE_BASELINE_ORG", "alpha-baseline")
    run = Mock(side_effect=AssertionError("escaped adapter executed"))
    monkeypatch.setattr(dispatcher.subprocess, "run", run)
    result = dispatcher.dispatch_parity("alpha-dev", "Synthetic comparison")
    assert result.status == "ERROR" and "config" in result.detail
    run.assert_not_called()


def test_valid_nested_parity_adapter_runs(root, monkeypatch):
    from jsc_qa import dispatcher
    client = root / "clients" / "alpha"
    adapter = client / "config" / "nested" / "parity.py"
    adapter.parent.mkdir()
    adapter.write_text("# trusted fixture", encoding="utf-8")
    (client / "config" / "parity.json").write_text(
        json.dumps({"script": "nested/parity.py", "baseline_org": "alpha-baseline"}), encoding="utf-8")
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="compared"))
    monkeypatch.setattr(dispatcher.subprocess, "run", run)
    with cli.delegated_context(client, "qa", []):
        assert dispatcher.dispatch_parity("alpha-dev", "Synthetic comparison").status == "PASS"
    assert run.call_args.args[0][1] == str(adapter)


def test_parity_dispatch_refuses_an_adapter_replaced_after_selection(root, monkeypatch):
    from jsc_qa import dispatcher
    client = root / "clients" / "alpha"
    adapter = client / "config" / "parity.py"
    adapter.write_text("# initial adapter", encoding="utf-8")
    foreign = root / "clients" / "beta" / "config" / "parity.py"
    foreign.write_text("# other client's adapter", encoding="utf-8")
    (client / "config" / "parity.json").write_text(
        json.dumps({"script": "parity.py", "baseline_org": "alpha-baseline"}), encoding="utf-8")
    run = Mock(side_effect=AssertionError("replacement adapter executed"))
    monkeypatch.setattr(dispatcher.subprocess, "run", run)
    with cli.delegated_context(client, "qa", []):
        adapter.unlink()
        try:
            adapter.symlink_to(foreign)
        except OSError:
            pytest.skip("symlinks unavailable")
        assert dispatcher.dispatch_parity("alpha-dev", "Synthetic comparison").status == "ERROR"
    run.assert_not_called()


@pytest.mark.parametrize("location", ["../beta/config", "artifacts"])
def test_parity_dispatch_refuses_a_config_directory_redirect(root, monkeypatch, location):
    from jsc_qa import dispatcher
    client = root / "clients" / "alpha"
    foreign = (client / location).resolve()
    (foreign / "parity.py").write_text("# synthetic foreign adapter", encoding="utf-8")
    (client / "config").rmdir()
    try:
        (client / "config").symlink_to(foreign, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    monkeypatch.setenv("TORQUE_WORKSPACE", str(client))
    monkeypatch.setenv("TORQUE_PARITY_SCRIPT", str(client / "config" / "parity.py"))
    monkeypatch.setenv("TORQUE_BASELINE_ORG", "alpha-baseline")
    run = Mock(side_effect=AssertionError("foreign config adapter executed"))
    monkeypatch.setattr(dispatcher.subprocess, "run", run)
    assert dispatcher.dispatch_parity("alpha-dev", "Synthetic comparison").status == "ERROR"
    run.assert_not_called()


@pytest.mark.parametrize("route,arguments", [
    ("lesson", ["capture", "--", "--help"]), ("qa", ["run", "Synthetic comparison"]),
    ("revert", ["data", "update", "--target-org", "alpha-dev"]),
    ("meeting", ["input.mp4"]), ("logs", ["--log-file", "sample.log"]),
])
def test_maintenance_refuses_delegates_before_import(root, monkeypatch, capsys, route, arguments):
    (root / ws.MAINTENANCE_FLAG).write_text("migration", encoding="utf-8")
    imported = Mock(side_effect=AssertionError("delegate imported during maintenance"))
    monkeypatch.setattr(cli.importlib, "import_module", imported)
    assert cli.main([route, "--workspace", str(root), "--client", "Alpha", *arguments]) == 2
    assert "maintenance" in capsys.readouterr().err
    imported.assert_not_called()


def test_maintenance_still_allows_package_help(root, capsys):
    (root / ws.MAINTENANCE_FLAG).write_text("migration", encoding="utf-8")
    with pytest.raises(SystemExit) as result:
        cli.main(["lesson", "--workspace", str(root), "--client", "Alpha", "--help"])
    assert result.value.code == 0
    assert "capture" in capsys.readouterr().out
    assert not (root / "clients" / "alpha" / "state").exists()


@pytest.mark.parametrize("mode", ["full", "build-only"])
@pytest.mark.parametrize("selection", ["client", "workspace", "cwd", "legacy"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_direct_revert_check_honors_maintenance_before_mode_shortcuts(root, monkeypatch, capsys,
                                                                   mode, selection, dry_run):
    from jsc_revert.wrappers import _common
    ws.set_ai_access(root, mode)
    client = root / "clients" / "alpha"
    if selection == "cwd":
        monkeypatch.chdir(client / "config")
    else:
        key = "JSC_ROOT" if selection == "legacy" else "TORQUE_WORKSPACE"
        monkeypatch.setenv(key, str(root if selection == "workspace" else client))
    (root / ws.MAINTENANCE_FLAG).write_text("migration", encoding="utf-8")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert _common.connected_approval("alpha-dev", "00D000000000001AAA", dry_run=dry_run) == (
        _common.EXIT_NOT_APPROVED, None)
    assert "maintenance" in capsys.readouterr().err
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_delegated_write_finishes_if_maintenance_starts_after_dispatch(root, monkeypatch):
    client = root / "clients" / "alpha"

    def finish(argv):
        (root / ws.MAINTENANCE_FLAG).write_text("migration", encoding="utf-8")
        (client / "state").mkdir(exist_ok=True)
        (client / "state" / "outcome.json").write_text('{"status":"complete"}', encoding="utf-8")
        return 0

    monkeypatch.setattr(cli.importlib, "import_module", lambda _: SimpleNamespace(main=finish))
    assert cli.main(["lesson", "capture", "synthetic", "--workspace", str(root), "--client", "Alpha"]) == 0
    assert json.loads((client / "state" / "outcome.json").read_text()) == {"status": "complete"}


def test_direct_revert_wrapper_pauses_before_snapshots_or_org_writes(root, monkeypatch, capsys):
    from jsc_revert import cli as revert_cli
    from jsc_revert.wrappers import _common

    client = root / "clients" / "alpha"
    monkeypatch.setenv("TORQUE_WORKSPACE", str(client))
    monkeypatch.setattr(_common.org_detect, "resolve_org", lambda _: SimpleNamespace(
        org_id_18="00D000000000001AAA"))
    run = Mock(side_effect=AssertionError("maintenance allowed an org write"))
    acquire = Mock(side_effect=AssertionError("maintenance allowed package state creation"))
    monkeypatch.setattr(_common, "run_sf_subprocess", run)
    monkeypatch.setattr(_common.WrapperContext, "acquire_org_lock", acquire)
    (root / ws.MAINTENANCE_FLAG).write_text("migration", encoding="utf-8")
    assert revert_cli.main(["data", "update", "--target-org", "alpha-dev", "--sobject", "Account",
                            "--record-id", "001000000000001AAA", "--values", "Name=Synthetic"]) == (
        _common.EXIT_NOT_APPROVED)
    assert "maintenance" in capsys.readouterr().err
    run.assert_not_called()
    acquire.assert_not_called()
    assert not (client / "state").exists()


def test_lesson_capture_pauses_without_writing_then_resumes(root, capsys):
    args = ["lesson", "--workspace", str(root), "--client", "Alpha", "capture", "Synthetic lesson"]
    state = root / "clients" / "alpha" / "state"
    (root / ws.MAINTENANCE_FLAG).write_text("migration", encoding="utf-8")
    assert cli.main(args) == 2
    assert "maintenance" in capsys.readouterr().err
    assert not state.exists()
    (root / ws.MAINTENANCE_FLAG).unlink()
    assert cli.main(args) == 0
    assert any(json.loads(p.read_text()).get("full_text") == "Synthetic lesson"
               for p in state.rglob("*.json"))
