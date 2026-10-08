from collections import namedtuple
import json
import os

import pytest

from torque import cli, cli_approval, workspace as ws
from torque.presence import Presence

YES = lambda: Presence(True, "")


@pytest.fixture
def connected(tmp_path):
    root = ws.init_workspace(tmp_path / "w", "Firm")
    ws.add_client(root, "Acme")
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    return root


def test_launch_refuses_without_consent(connected):
    with pytest.raises(ws.WorkspaceError, match="consent"):
        cli_approval.launch(connected, "Acme", [], execvp=lambda *a: None, presence=YES)


def test_launch_refuses_without_operator(connected):
    with pytest.raises(ws.WorkspaceError, match="terminal"):
        cli_approval.launch(connected, "Acme", [], execvp=lambda *a: None,
                            presence=lambda: Presence(False, "needs a real terminal"))


def test_launch_refuses_outside_connected_mode(tmp_path):
    root = ws.init_workspace(tmp_path / "w", "Firm")
    ws.add_client(root, "Acme")
    with pytest.raises(ws.WorkspaceError, match="connected"):
        cli_approval.launch(root, "Acme", [], execvp=lambda *a: None, presence=YES)


def test_launch_binds_client_in_environment(connected, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in ("TORQUE_CLIENT", "TORQUE_WORKSPACE"):
        # setenv first so teardown restores the original state after launch() sets it.
        monkeypatch.setenv(key, "placeholder")
        monkeypatch.delenv(key)
    monkeypatch.setattr("torque.consent.load_consent", lambda w, c: {"schema": "torque.consent/1", "client": "acme",
                                                                     "status": "active",
                                                                     "reviewer": {"name": "R", "signed_off_at":
                                                                                  "2026-09-30T10:00:00+00:00"},
                                                                     "data_allowed": ["metadata"],
                                                                     "approved_orgs": [{"alias": "a", "kind": "sandbox",
                                                                                        "org_id_18": "x"}]})
    seen = {}

    def fake_exec(program, args):
        seen.update(program=program, args=args, client=os.environ.get("TORQUE_CLIENT"),
                    workspace=os.environ.get("TORQUE_WORKSPACE"), cwd=os.getcwd())

    cli_approval.launch(connected, "Acme", ["--model", "x"], execvp=fake_exec, presence=YES)
    assert seen["program"] == "claude" and seen["args"] == ["claude", "--model", "x"]
    assert seen["client"] == "acme" and seen["workspace"] == str(connected / "clients" / "acme")
    assert os.path.realpath(seen["cwd"]) == os.path.realpath(connected)


def test_run_agent_waits_for_the_agent_and_returns_its_exit_code():
    import signal
    import sys
    before = signal.getsignal(signal.SIGINT)
    assert cli_approval.run_agent([sys.executable, "-c", "import sys; sys.exit(7)"]) == 7
    assert signal.getsignal(signal.SIGINT) is before
    with pytest.raises(ws.WorkspaceError, match="could not start"):
        cli_approval.run_agent(["no-such-agent-binary-for-this-test"])
    assert signal.getsignal(signal.SIGINT) is before


@pytest.mark.skipif(os.name != "nt", reason="exec replaces the process everywhere else")
def test_launch_starts_the_agent_as_a_child_on_windows(connected, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in ("TORQUE_CLIENT", "TORQUE_WORKSPACE", "TORQUE_LAUNCH"):
        monkeypatch.setenv(key, "placeholder")
        monkeypatch.delenv(key)
    monkeypatch.setattr("torque.consent.load_consent", lambda w, c: {
        "schema": "torque.consent/1", "client": "acme", "status": "active",
        "reviewer": {"name": "R", "signed_off_at": "2026-09-30T10:00:00+00:00"}, "data_allowed": ["metadata"],
        "approved_orgs": [{"alias": "a", "kind": "sandbox", "org_id_18": "x"}]})
    monkeypatch.setattr("torque.presence.confirm_code", lambda: True)
    monkeypatch.setattr("torque.presence.operator_present", YES)
    seen = {}

    def fake_agent(argv):
        seen.update(argv=argv, client=os.environ.get("TORQUE_CLIENT"), launch=os.environ.get("TORQUE_LAUNCH"))
        return 5
    monkeypatch.setattr(cli_approval, "run_agent", fake_agent)
    # The default exec is used: on Windows it would end this process and leave the console to the shell.
    assert cli_approval.launch(connected, "Acme", ["--model", "x"]) == 5
    assert seen["argv"] == ["claude", "--model", "x"] and seen["client"] == "acme" and seen["launch"]


CONSENT = {"schema": "torque.consent/1", "client": "acme", "status": "active",
           "reviewer": {"name": "R", "signed_off_at": "2026-09-30T10:00:00+00:00"}, "data_allowed": ["metadata"],
           "approved_orgs": [{"alias": "a", "kind": "sandbox", "org_id_18": "x"}]}


@pytest.fixture
def ready(connected, tmp_path, monkeypatch):
    """A connected workspace whose client has usable consent, and an environment
    the launch may write into."""
    monkeypatch.chdir(tmp_path)
    for key in ("TORQUE_CLIENT", "TORQUE_WORKSPACE", "TORQUE_LAUNCH"):
        monkeypatch.setenv(key, "placeholder")
        monkeypatch.delenv(key)
    monkeypatch.setattr("torque.consent.load_consent", lambda w, c: dict(CONSENT))
    return connected


def started(root, extra=(), **options):
    """(program, argv, the launch record) for one owner launch."""
    seen = {}
    cli_approval.launch(root, "Acme", list(extra), execvp=lambda program, args: seen.update(program=program, args=args),
                        presence=YES, **options)
    record = root / "clients" / "acme" / "approvals" / "consumed" / (os.environ["TORQUE_LAUNCH"] + ".launch")
    return seen["program"], seen["args"], json.loads(record.read_text(encoding="utf-8"))


def name_host(root, host):
    config = json.loads((root / "workspace.json").read_text(encoding="utf-8"))
    (root / "workspace.json").write_text(json.dumps({**config, "host": host}), encoding="utf-8")


def test_launch_starts_claude_code_unless_a_host_is_named(ready):
    program, args, record = started(ready, ["--model", "x"])
    assert (program, args, record["host"]) == ("claude", ["claude", "--model", "x"], "claude")


@pytest.mark.parametrize("named", ["antigravity", "agy", "gemini"])
def test_launch_starts_the_host_named_on_the_command_line(ready, named):
    program, args, record = started(ready, ["-p", "hello", "--model", "m"], host=named)
    assert (program, args) == ("agy", ["agy", "-p", "hello", "--model", "m"])
    assert record["host"] == "antigravity" and record["via"] == "presence"


def test_launch_starts_the_host_the_workspace_names(ready):
    name_host(ready, "antigravity")
    program, args, record = started(ready)
    assert (program, args, record["host"]) == ("agy", ["agy"], "antigravity")
    # --host still decides for one launch.
    program, args, record = started(ready, host="claude")
    assert (program, args, record["host"]) == ("claude", ["claude"], "claude")


def test_launch_refuses_an_unknown_host_before_it_writes_a_record(ready):
    consumed = ready / "clients" / "acme" / "approvals" / "consumed"
    for change, options in ((None, {"host": "cursor"}), ("cursor", {})):
        if change:
            name_host(ready, change)
        ran = []
        with pytest.raises(ValueError, match="unknown host"):
            cli_approval.launch(ready, "Acme", [], execvp=lambda *a: ran.append(a), presence=YES, **options)
        assert not ran and not list(consumed.glob("*.launch")) and "TORQUE_LAUNCH" not in os.environ


def test_launch_does_not_hand_on_a_hook_host_from_the_shell(ready, monkeypatch):
    """The hook states its own host; one inherited from the launching shell would
    make Claude Code's hook refuse its own launch record."""
    from torque import hosts
    monkeypatch.setenv(hosts.HOOK_HOST_ENV, "antigravity")
    seen = []
    cli_approval.launch(ready, "Acme", [], execvp=lambda *a: seen.append(os.environ.get(hosts.HOOK_HOST_ENV)),
                        presence=YES)
    assert seen == [None]


def test_a_delegated_launch_checks_its_options_against_the_host_it_starts(ready, monkeypatch):
    """The tier 2 checks and the claim are stand-ins here: they need separate OS
    accounts, and test_launch_binding.py runs the real ones on macOS and Linux.
    What this covers on every platform is the host and that host's own lists."""
    from torque import delegation, launch as launches
    config = json.loads((ready / "workspace.json").read_text(encoding="utf-8"))
    named = {}
    claims, ran = [], []
    monkeypatch.setattr(launches, "_tier2_config", lambda workspace, **k: (ready, {**config, **named}, None))
    monkeypatch.setattr(launches, "claim_binding",
                        lambda root, client, binding, **k: claims.append(k["host"]) or {"id": binding})
    monkeypatch.delenv("CLAUDE_CODE_SIMPLE", raising=False)

    def start(words, **options):
        return cli_approval.launch(ready, "Acme", words, execvp=lambda program, args: ran.append((program, args)),
                                   delegated=True, binding="lnk-0123456789ab", **options)

    def refused(words, binary, **options):
        with pytest.raises(delegation.Refusal) as info:
            start(words, **options)
        assert info.value.reason_class == "launch-flag-refused" and f"to {binary}:" in str(info.value), str(info.value)

    harness = ["-p", "--input-format", "stream-json", "--verbose", "--permission-mode", "default"]
    # Claude Code, named or by default: its list as before, and never --add-dir.
    refused(["-p", "--add-dir", "/tmp"], "claude")
    refused(["--dangerously-skip-permissions"], "claude", host="claude")
    start(harness)
    # Antigravity has its own, shorter list.
    refused(harness, "agy", host="antigravity")
    refused(["-p", "--dangerously-skip-permissions"], "agy", host="agy")
    start(["-p", "hello", "--add-dir", "/work/shared"], host="antigravity")
    # The workspace's host is the default, and --host still decides for one launch.
    named["host"] = "antigravity"
    refused(harness, "agy")
    start(["-p", "hello"])
    start(harness, host="claude")
    assert ran == [("claude", ["claude", *harness]), ("agy", ["agy", "-p", "hello", "--add-dir", "/work/shared"]),
                   ("agy", ["agy", "-p", "hello"]), ("claude", ["claude", *harness])]
    assert claims == ["claude", "antigravity", "antigravity", "claude"]
    # A variable one host refuses is that host's concern only.
    monkeypatch.setenv("CLAUDE_CODE_SIMPLE", "1")
    with pytest.raises(delegation.Refusal, match="CLAUDE_CODE_SIMPLE"):
        start(harness, host="claude")
    start(["-p", "hello"], host="antigravity")
    assert os.environ["CLAUDE_CODE_SIMPLE"] == "1" and len(claims) == 5


def test_the_launch_command_passes_its_host_and_the_words_after_the_dashes(connected, monkeypatch):
    seen = []
    monkeypatch.setattr(cli_approval, "launch", lambda *a, **k: seen.append((a, k)) or 0)
    base = ["launch", "--workspace", str(connected), "--client", "Acme"]
    assert cli.main([*base, "--host", "antigravity", "--", "-p", "hello"]) == 0
    assert cli.main(base) == 0
    assert cli.main([*base, "--delegated", "--binding", "lnk-0123456789ab", "--host", "agy"]) == 0
    assert seen[0] == ((str(connected), "Acme", ["-p", "hello"]), {"host": "antigravity"})
    assert seen[1] == ((str(connected), "Acme", []), {"host": None})
    assert seen[2][1] == {"delegated": True, "binding": "lnk-0123456789ab", "host": "agy"}


def test_launch_help_names_both_hosts(capsys):
    with pytest.raises(SystemExit):
        cli.main(["launch", "--help"])
    text = capsys.readouterr().out
    assert "--host claude|antigravity" in text and "workspace.json" in text


def test_the_binding_message_names_the_workspaces_host(connected, monkeypatch, capsys):
    from types import SimpleNamespace
    record = {"id": "lnk-0123456789ab", "client": "acme", "expires_at": "2026-09-30T10:10:00+00:00"}
    monkeypatch.setattr("torque.launch.create_binding", lambda *a, **k: record)
    p = SimpleNamespace(workspace=str(connected), client="Acme", model_id=None, minutes=10, json=False)
    assert cli_approval._launch_binding(p) == 0
    assert "--binding lnk-0123456789ab -- CLAUDE OPTIONS" in capsys.readouterr().out
    name_host(connected, "antigravity")
    assert cli_approval._launch_binding(p) == 0
    assert "--binding lnk-0123456789ab -- AGY OPTIONS" in capsys.readouterr().out


def test_require_exit_codes(connected, monkeypatch, capsys):
    monkeypatch.setattr("torque.approval.require", lambda *a, **k: (False, "no granted approval matches"))
    code = cli.main(["approval", "require", "--workspace", str(connected), "--client", "Acme", "--org", "a",
                     "--", "sf", "apex", "run", "-o", "a"])
    assert code == 3 and "no granted approval" in capsys.readouterr().err
    seen = {}

    def ok(*a, **k):
        seen["argv"] = a[3]
        return True, "apr-000000000001"
    monkeypatch.setattr("torque.approval.require", ok)
    assert cli.main(["approval", "require", "--workspace", str(connected), "--client", "Acme", "--org", "a",
                     "--", "sf", "apex", "run", "-o", "a"]) == 0
    assert seen["argv"] == ["sf", "apex", "run", "-o", "a"]


def test_ai_access_connected_parses(tmp_path, monkeypatch):
    root = ws.init_workspace(tmp_path / "w", "Firm")
    monkeypatch.setattr("torque.presence.operator_present", lambda *a, **k: Presence(True, ""))
    monkeypatch.setattr("torque.presence.confirm_code", lambda *a, **k: True)
    assert cli.main(["workspace", "ai-access", "connected", "--approval", "required", "--path", str(root)]) == 0
    assert json.loads((root / "workspace.json").read_text(encoding="utf-8"))["approval"] == "required"


def test_ai_access_connected_refused_from_agent(tmp_path, capsys):
    root = ws.init_workspace(tmp_path / "w", "Firm")
    assert cli.main(["workspace", "ai-access", "connected", "--approval", "required", "--path", str(root)]) == 2
    assert "terminal" in capsys.readouterr().err


def test_consent_and_request_through_the_cli(connected, tmp_path, monkeypatch, capsys):
    Org = namedtuple("Org", "org_id_18 detected_org_type")
    monkeypatch.setattr("torque.presence.operator_present", lambda *a, **k: Presence(True, ""))
    monkeypatch.setattr("torque.presence.confirm_code", lambda *a, **k: True)
    monkeypatch.setattr("jsc_revert.org_detect.resolve_org",
                        lambda alias, **k: Org("00D000000000001AAA", "sandbox") if alias == "acme-sbx" else None)
    monkeypatch.setattr("jsc_revert.intent_marker._current_user_name", lambda: "consultant")
    letter = tmp_path / "agreement.pdf"
    letter.write_bytes(b"synthetic")
    base = ["--workspace", str(connected), "--client", "Acme"]
    assert cli.main(["client", "consent", "record", *base, "--agreed-on", "2026-09-30", "--evidence", str(letter),
                     "--data", "metadata", "--org", "acme-sbx", "--suspend-contact", "Contact"]) == 0
    assert cli.main(["client", "consent", "sign-off", *base, "--reviewer", "Reviewer"]) == 0
    capsys.readouterr()
    assert cli.main(["client", "consent", "show", *base, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "active"
    from torque import changes
    cid = changes.create_change(connected, "Acme", "Fix", "Works", [], "acme-sbx")["id"]
    monkeypatch.chdir(tmp_path)
    (tmp_path / "x.apex").write_text("System.debug(1);", encoding="utf-8")
    assert cli.main(["approval", "request", *base, "--change", cid, "--org", "acme-sbx", "--",
                     "sf", "apex", "run", "-f", "x.apex", "-o", "acme-sbx"]) == 0
    out = capsys.readouterr().out
    assert "torque approval grant req-" in out and "sf apex run -f x.apex -o acme-sbx" in out
    assert cli.main(["approval", "list", *base, "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert len(listed["requests"]) == 1 and listed["approvals"] == []
    assert cli.main(["approval", "log", *base]) == 0
    assert "approval_request" in capsys.readouterr().out


def test_invocation_is_recorded():
    cli.main(["workflows", "list", "--json"])
    assert cli.INVOCATION == ("torque", ["workflows", "list", "--json"])
