"""The gate as an Antigravity hook: the same decisions for Antigravity's tool
calls, in Antigravity's answer format. No Antigravity session is run."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from torque import cli, gate, gate_antigravity as agy, hosts

SRC = str(Path(__file__).resolve().parents[1] / "src")


@pytest.fixture
def root(tmp_path, monkeypatch):
    base = tmp_path / "firm"
    for folder in ("clients/acme", "src", ".agents", ".claude"):
        (base / folder).mkdir(parents=True)
    (base / "clients/acme/notes.md").write_text("x", encoding="utf-8")
    (base / "src/a.md").write_text("x", encoding="utf-8")
    set_mode(base, "build-only")
    # main() binds the session to its folder through the first variable and states
    # its host in the second; set here so the teardown takes both out again.
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "")
    monkeypatch.setenv(hosts.HOOK_HOST_ENV, "")
    return base


def set_mode(root, mode):
    config = {"schema": "torque.workspace/1", "name": "Synthetic", "profile": "generic"}
    if mode:
        config["ai_access"] = mode
    (root / "workspace.json").write_text(json.dumps(config), encoding="utf-8")


def payload(root, name, args, **extra):
    return {"toolCall": {"name": name, "args": args}, "workspacePaths": [root.as_posix()],
            "conversationId": "c1", "stepIdx": 1, **extra}


def answer(monkeypatch, capsys, data):
    raw = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8"))
    assert agy.main() == 0
    return json.loads(capsys.readouterr().out)


def decision(monkeypatch, capsys, root, name, args):
    return answer(monkeypatch, capsys, payload(root, name, args))["decision"]


def blocked(root):
    notes = str(root / "clients" / "acme" / "notes.md")
    return [
        ("view_file", {"AbsolutePath": notes}),
        ("list_dir", {"DirectoryPath": str(root / "clients")}),
        ("grep_search", {"Query": "secret", "SearchPath": str(root)}),
        ("grep_search", {"Query": "secret"}),
        ("find_by_name", {"Pattern": "*.md", "SearchDirectory": str(root)}),
        ("run_command", {"CommandLine": "sf data query -o example -q 'SELECT Id FROM Account'", "Cwd": str(root)}),
        ("run_command", {"CommandLine": "torque context --workspace . --client acme", "Cwd": str(root)}),
        ("run_command", {"CommandLine": "cat clients/acme/notes.md"}),
        ("run_command", {"CommandLine": "cat acme/notes.md", "Cwd": "clients"}),
        ("manage_task", {"Action": "send_input", "Input": "sf data query -o example", "TaskId": "1"}),
        ("send_command_input", {"Input": "sf data query -o example"}),
        ("write_to_file", {"TargetFile": str(root / "workspace.json"), "CodeContent": "{}"}),
        ("write_to_file", {"TargetFile": str(root / ".agents" / "hooks.json"), "CodeContent": "{}"}),
        ("replace_file_content", {"TargetFile": notes}),
        ("multi_replace_file_content", {"TargetFile": notes}),
        ("sed_file", {"TargetFile": notes}),
        ("call_mcp_tool", {"ServerName": "salesforce", "ToolName": "run_soql_query", "Arguments": {"query": "x"}}),
        ("call_mcp_tool", {"ServerName": "files", "ToolName": "read", "Arguments": json.dumps({"path": notes})}),
        ("open_browser_url", {"Url": (root / "clients" / "acme" / "notes.md").as_uri()}),
        # Tools that carry a prompt or an address have their arguments checked as paths too.
        ("browser_subagent", {"Task": "summarize it", "Start": (root / "clients" / "acme" / "notes.md").as_uri()}),
        ("schedule", {"Prompt": notes}),
        ("read_url_content", {"Url": (root / "clients" / "acme" / "notes.md").as_uri()}),
        # A known tool without the argument that names its file, and tools this module does not know.
        ("view_file", {}),
        ("multi_replace_file_content", {"Chunks": []}),
        ("notebook_execution", {"Code": "x"}),
        ("a_tool_added_later", {"X": "y"}),
        # Text typed into a running command that this module cannot read.
        ("send_command_input", {}),
        ("send_command_input", {"Input": ["sf", "data", "query"]}),
        ("manage_task", {"Action": "send_input", "TaskId": "1"}),
        ("manage_task", {"Action": "SendInput", "TaskId": "1", "Text": "sf data query -o example"}),
        ("manage_task", {"Action": "send_input", "Input": 5}),
        ("manage_task", {"Action": "list", "Input": None}),
        ("manage_task", {"Action": {"kind": "send_input"}}),
        # An MCP call that does not say which server's tool it is.
        ("call_mcp_tool", {"ToolName": "add", "Arguments": {"text": "hello"}}),
        ("call_mcp_tool", {"ServerName": "notes", "Arguments": {"text": "hello"}}),
        ("call_mcp_tool", {"ServerName": "", "ToolName": "add"}),
        ("call_mcp_tool", {"ServerName": 5, "ToolName": "add"}),
        ("call_mcp_tool", {"ServerName": "notes__salesforce", "ToolName": "add"}),
        ("call_mcp_tool", {"ServerName": "notes", "ToolName": "_add"}),
    ]


def test_build_only_blocks_the_same_calls_in_antigravity(root, monkeypatch, capsys):
    for name, args in blocked(root):
        reply = answer(monkeypatch, capsys, payload(root, name, args))
        assert reply["decision"] == "deny" and reply["reason"].startswith("Build-only mode"), (name, args, reply)


def test_what_the_gate_lets_through_is_allowed_or_handed_to_antigravity(root, monkeypatch, capsys, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    cases = [
        # Reads inside the folders the session was started with.
        ("allow", "view_file", {"AbsolutePath": str(root / "src" / "a.md")}),
        ("allow", "list_dir", {"DirectoryPath": str(root)}),
        ("allow", "grep_search", {"Query": "x", "SearchPath": str(root / "src")}),
        ("allow", "find_by_name", {"Pattern": "*.md", "SearchDirectory": str(root / "src")}),
        # Everything else goes to Antigravity's own permission flow.
        ("ask", "invoke_subagent", {"Subagents": [{"Prompt": "x"}]}),
        ("ask", "ask_question", {"Question": "x"}),
        ("ask", "manage_task", {"Action": "list"}),
        ("ask", "view_file", {"AbsolutePath": str(outside / "x.md")}),
        ("ask", "run_command", {"CommandLine": "git status", "Cwd": str(root)}),
        ("ask", "manage_task", {"Action": "send_input", "Input": "git status", "TaskId": "1"}),
        ("ask", "write_to_file", {"TargetFile": str(root / "src" / "b.md"), "CodeContent": "x"}),
        ("ask", "call_mcp_tool", {"ServerName": "notes", "ToolName": "add", "Arguments": {"text": "hello"}}),
        ("ask", "search_web", {"Query": "x"}),
        ("ask", "schedule", {"Prompt": "run the tests again in an hour"}),
        ("ask", "read_url_content", {"Url": "https://example.com/page"}),
        ("ask", "manage_task", {"Action": "kill", "TaskId": "1"}),
        ("ask", "manage_task", {"TaskId": "1"}),
    ]
    for expected, name, args in cases:
        assert decision(monkeypatch, capsys, root, name, args) == expected, (name, args)


def test_build_only_blocks_antigravitys_own_browser_tools(root, monkeypatch, capsys):
    # A browser can reach an org through a login the session did not make, so build-only
    # blocks browser tools by name, whatever page they are sent to.
    for name, args in (("open_browser_url", {"Url": "https://example.com"}),
                       ("browser_subagent", {"Task": "open the release notes"}),
                       ("read_browser_page", {"PageId": "1"})):
        reply = answer(monkeypatch, capsys, payload(root, name, args))
        assert reply["decision"] == "deny" and reply["reason"].startswith("Build-only mode"), (name, reply)


def test_a_folder_added_to_the_session_counts_as_inside_it(root, monkeypatch, capsys, tmp_path):
    added = tmp_path / "added"
    added.mkdir()
    call = payload(root, "view_file", {"AbsolutePath": str(added / "x.md")})
    assert answer(monkeypatch, capsys, call)["decision"] == "ask"
    call["workspacePaths"].append(added.as_posix())
    assert answer(monkeypatch, capsys, call)["decision"] == "allow"


def test_without_build_only_nothing_is_denied(root, monkeypatch, capsys):
    set_mode(root, None)
    for name, args in blocked(root):
        assert decision(monkeypatch, capsys, root, name, args) in ("allow", "ask"), (name, args)


def test_the_protected_records_stay_protected_with_the_gate_off(root, monkeypatch, capsys):
    set_mode(root, None)
    (root / ".torque").mkdir()
    (root / ".torque" / "templates.json").write_text("{}", encoding="utf-8")
    consent = str(root / "clients" / "acme" / "consent.json")
    assert decision(monkeypatch, capsys, root, "write_to_file", {"TargetFile": consent, "CodeContent": "{}"}) == "deny"
    command = {"CommandLine": "rm clients/acme/consent.json", "Cwd": str(root)}
    assert decision(monkeypatch, capsys, root, "run_command", command) == "deny"


def test_leaving_the_working_folder_keeps_the_session_gated(root, monkeypatch, capsys):
    call = {"CommandLine": "sf data query -o example", "Cwd": str(root.parent)}
    assert decision(monkeypatch, capsys, root, "run_command", call) == "deny"


def test_input_that_cannot_be_read_is_denied(root, monkeypatch, capsys):
    for raw in (b"not json", b"[]", json.dumps({"toolCall": {"args": {}}}).encode(),
                json.dumps({"toolCall": {"name": "run_command", "args": {}}}).encode(),
                json.dumps({"toolCall": {"name": "view_file", "args": "oops"}}).encode()):
        reply = answer(monkeypatch, capsys, raw)
        assert reply["decision"] == "deny" and "fail closed" in reply["reason"], raw


def test_an_event_without_a_tool_call_gets_no_decision(root, monkeypatch, capsys):
    assert answer(monkeypatch, capsys, {"stepIdx": 3, "workspacePaths": [root.as_posix()]}) == {}


def test_the_blocked_reason_names_antigravity_tools(root, monkeypatch, capsys):
    reply = answer(monkeypatch, capsys, payload(root, "a_tool_added_later", {}))
    assert "run_command" in reply["reason"] and "Bash" not in reply["reason"]
    assert hosts.ANTIGRAVITY.tool_hint in reply["reason"]
    # The registry holds the gate's own wording, or the swap above would do nothing.
    assert hosts.CLAUDE.tool_hint in gate.decide("a_tool_added_later", {}, root, "build-only", root)[1]


def test_the_hook_states_its_host_before_it_calls_the_gate(root, monkeypatch, capsys):
    seen = []
    real = gate.evaluate

    def evaluate(raw=None):
        seen.append(os.environ.get(hosts.HOOK_HOST_ENV))
        return real(raw)
    monkeypatch.setattr(gate, "evaluate", evaluate)
    answer(monkeypatch, capsys, payload(root, "run_command", {"CommandLine": "git status"}))
    assert seen == ["antigravity"]


def test_connected_mode_treats_an_antigravity_session_as_unbound(root, monkeypatch, capsys):
    config = json.loads((root / "workspace.json").read_text(encoding="utf-8"))
    (root / "workspace.json").write_text(json.dumps({**config, "ai_access": "connected", "approval": "required"}),
                                         encoding="utf-8")
    reply = answer(monkeypatch, capsys, payload(root, "run_command", {"CommandLine": "sf data query -o example"}))
    assert reply["decision"] == "deny" and "no client is bound" in reply["reason"]
    notes = {"AbsolutePath": str(root / "clients" / "acme" / "notes.md")}
    assert decision(monkeypatch, capsys, root, "view_file", notes) == "deny"


def test_the_shell_tool_follows_the_platform(root):
    event = agy.gate_event(payload(root, "run_command", {"CommandLine": "git status"}))
    assert event["tool_name"] == ("PowerShell" if os.name == "nt" else "Bash")
    assert event["tool_input"] == {"command": "git status"} and event["cwd"] == root.as_posix()
    assert event["session_id"] == "c1"


def test_the_call_id_is_the_conversation_and_the_step(root):
    call = payload(root, "run_command", {"CommandLine": "git status"})
    assert agy.gate_event(call)["tool_use_id"] == "c1:1"
    assert agy.gate_event({**call, "stepIdx": 12})["tool_use_id"] == "c1:12"
    # Without both parts there is no ID, rather than one made of half of them.
    for change in ({"stepIdx": None}, {"stepIdx": "1"}, {"stepIdx": True}, {"conversationId": ""},
                   {"conversationId": 7}):
        assert agy.gate_event({**call, **change})["tool_use_id"] is None, change


def test_calls_are_rewritten_into_the_gates_tools(root):
    def event(name, args):
        made = agy.gate_event(payload(root, name, args))
        return made["tool_name"], made["tool_input"]
    assert event("view_file", {"AbsolutePath": "a.md"}) == ("Read", {"file_path": "a.md"})
    assert event("list_dir", {}) == ("LS", {"path": root.as_posix()})
    assert event("grep_search", {"Query": "x", "SearchPath": "src", "Includes": ["*.md", "*.txt"]}) == (
        "Grep", {"pattern": "x", "path": "src", "glob": "*.md,*.txt"})
    assert event("find_by_name", {"Pattern": "*.md"}) == ("Glob", {"pattern": "*.md"})
    assert event("replace_file_content", {"TargetFile": "a.md"}) == ("Edit", {"file_path": "a.md"})
    assert event("call_mcp_tool", {"ServerName": "s", "ToolName": "t", "Arguments": '{"a": 1}'}) == (
        "mcp__s__t", {"a": 1})
    assert event("call_mcp_tool", {"ServerName": "s", "ToolName": "t", "Arguments": "plain"}) == (
        "mcp__s__t", {"value": "plain"})
    # Antigravity's browser tools get a server name connected mode reads as a browser.
    assert event("browser_get_dom", {"PageId": "1"}) == (
        "mcp__antigravity_browser__browser_get_dom", {"PageId": "1"})
    assert event("browser_subagent", {"Task": "x"}) == ("mcp__antigravity_browser__browser_subagent", {"Task": "x"})
    assert event("schedule", {"Prompt": "x"}) == ("mcp__antigravity__schedule", {"Prompt": "x"})
    assert event("read_url_content", {"Url": "https://example.com"}) == (
        "mcp__antigravity__read_url_content", {"Url": "https://example.com"})
    assert event("search_web", {"Query": "x"}) == ("Task", {})
    assert event("manage_task", {"Action": "list"}) == ("Task", {})
    assert event("a_tool_added_later", {"X": "y"}) == ("a_tool_added_later", {"X": "y"})


def test_a_known_tool_this_module_cannot_read_keeps_its_own_name(root):
    """So build-only and connected mode block it as a tool they do not recognise,
    instead of passing it as one with nothing to check."""
    for name, args in (("send_command_input", {}), ("send_command_input", {"Input": None}),
                       ("manage_task", {"Action": "send_input"}), ("manage_task", {"Action": "list", "Input": 1}),
                       ("call_mcp_tool", {"Arguments": {"a": 1}}), ("call_mcp_tool", {"ServerName": "s"}),
                       ("call_mcp_tool", {"ToolName": "t"}), ("call_mcp_tool", {"ServerName": " s", "ToolName": "t"}),
                       ("call_mcp_tool", {"ServerName": "s__x", "ToolName": "t"}),
                       ("call_mcp_tool", {"ServerName": "s", "ToolName": "t_"})):
        made = agy.gate_event(payload(root, name, args))
        assert (made["tool_name"], made["tool_input"]) == (name, args), (name, args)
        assert gate.decide(made["tool_name"], made["tool_input"], root, "build-only", root)[0] is False


def test_every_tool_this_module_names_is_one_antigravity_lists():
    # The tools Antigravity CLI 1.3.1 lists at the start of a session.
    listed = set("""ask_custom_permission ask_permission ask_question browser_click_element
        browser_drag_pixel_to_pixel browser_get_dom browser_get_network_request browser_input
        browser_list_network_requests browser_mouse_down browser_mouse_up browser_move_mouse browser_press_key
        browser_refresh_page browser_resize_window browser_scroll browser_scroll_dom browser_select_option
        browser_subagent call_mcp_tool capture_browser_console_logs capture_browser_screenshot click_browser_pixel
        command_status define_subagent delete_knowledge execute_browser_javascript find_by_name finish
        generate_image grep_search invoke_subagent list_browser_pages list_dir list_permissions
        list_plugin_accounts list_resources manage_inbox manage_subagents manage_task multi_replace_file_content
        notebook_edit notebook_execution open_browser_url read_browser_page read_resource read_url_content
        replace_file_content run_command run_workflow schedule search_marketplace search_web sed_file
        send_command_input send_message view_file wait wait_5_seconds write_to_file""".split())
    named = (set(agy.FILE_TOOLS) | set(agy.SEARCH_TOOLS) | set(agy.INPUT_TOOLS) | agy.PLAIN_TOOLS
             | agy.NETWORK_TOOLS | agy.SCANNED_TOOLS | {"run_command", "call_mcp_tool"})
    assert named <= listed, sorted(named - listed)
    # Running code in a notebook is left unknown on purpose: build-only blocks it.
    assert listed - named == {"notebook_execution"}


def test_the_time_budget_ends_in_a_denial(monkeypatch):
    stream = io.StringIO()

    def stop(code):
        raise SystemExit(code)
    monkeypatch.setattr(agy.os, "_exit", stop)
    with pytest.raises(SystemExit) as ended:
        agy._out_of_time(stream)
    assert ended.value.code == 0 and json.loads(stream.getvalue())["decision"] == "deny"


def test_the_hook_command_is_isolated_and_quoted_only_when_needed():
    assert agy.hook_command("/opt/venv/bin/python") == "/opt/venv/bin/python -I -m torque.gate_antigravity"
    assert agy.hook_command(r"C:\Program Files\venv\python.exe") == (
        r'"C:\Program Files\venv\python.exe" -I -m torque.gate_antigravity')
    entry = agy.hook_entry("/opt/venv/bin/python")[agy.HOOK_NAME]["PreToolUse"][0]
    assert entry["matcher"] == "*" and entry["hooks"][0]["timeout"] == agy.HOOK_TIMEOUT


def run_hook(root, data, command=None):
    """The hook as Antigravity starts it: through the shell, from `.agents/`."""
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    command = command or f'"{sys.executable}" -m torque.gate_antigravity'
    return subprocess.run(command, shell=True, cwd=root / ".agents", input=json.dumps(data), capture_output=True,
                          text=True, env=env, timeout=120)


def test_the_hook_runs_through_the_shell_from_the_agents_folder(root):
    done = run_hook(root, payload(root, "view_file", {"AbsolutePath": str(root / "clients" / "acme" / "notes.md")}))
    assert done.returncode == 0 and json.loads(done.stdout)["decision"] == "deny", done.stderr
    done = run_hook(root, payload(root, "view_file", {"AbsolutePath": str(root / "src" / "a.md")}))
    assert json.loads(done.stdout)["decision"] == "allow", done.stderr
    # Without the workspace paths the folder above .agents is the session's folder.
    done = run_hook(root, {"toolCall": {"name": "run_command", "args": {"CommandLine": "sf data query -o example"}}})
    assert json.loads(done.stdout)["decision"] == "deny", done.stderr


def test_a_hook_that_cannot_load_the_gate_prints_no_decision(root):
    done = run_hook(root, payload(root, "view_file", {"AbsolutePath": "a.md"}),
                    f'"{sys.executable}" -I -c "import nothing_by_this_name"')
    assert done.returncode != 0 and not done.stdout.strip()


def write_hooks(root, command, matcher="*", **spec):
    entry = {agy.HOOK_NAME: {"PreToolUse": [{"matcher": matcher, "hooks": [{"type": "command", "command": command}]}],
                             **spec}}
    (root / ".agents" / "hooks.json").write_text(json.dumps(entry), encoding="utf-8")


def test_doctor_reports_the_antigravity_hook(root, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", SRC + os.pathsep + os.environ.get("PYTHONPATH", ""))
    report = cli._antigravity_hook_report(root, "build-only")
    assert not report["configured"] and report["verified"] is None
    assert agy.HOOK_MODULE in report["recommended_entry"][agy.HOOK_NAME]["PreToolUse"][0]["hooks"][0]["command"]

    working = f'"{sys.executable}" -m {agy.HOOK_MODULE}'
    write_hooks(root, working)
    report = cli._antigravity_hook_report(root, "build-only")
    assert report["configured"] and report["verified"] and report["probe_decision"] == "deny", report
    assert report["matcher_covers_tools"] and not report["isolated"] and not report["disabled"]

    write_hooks(root, working, matcher="run_command")
    assert not cli._antigravity_hook_report(root, "build-only")["matcher_covers_tools"]
    write_hooks(root, working, enabled=False)
    assert cli._antigravity_hook_report(root, "build-only")["disabled"]

    write_hooks(root, f'"{sys.executable}" -I -c "import nothing_by_this_name" # {agy.HOOK_MODULE}')
    report = cli._antigravity_hook_report(root, "build-only")
    assert report["configured"] and report["isolated"] and report["verified"] is False
    assert report["probe_decision"] is None

    # Outside build-only mode nothing is run.
    assert cli._antigravity_hook_report(root, "full")["verified"] is None


def test_doctor_tells_the_owner_when_antigravity_is_not_gated(tmp_path, capsys, monkeypatch):
    from torque import workspace as ws
    monkeypatch.setenv("PYTHONPATH", SRC + os.pathsep + os.environ.get("PYTHONPATH", ""))
    root = ws.init_workspace(tmp_path / "firm", "Example firm")
    ws.set_ai_access(root, "build-only")
    cli.main(["doctor", "--workspace", str(root), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert any(".agents/hooks.json" in action and agy.HOOK_MODULE in action for action in report["next_actions"])

    write_hooks(Path(root), f'"{sys.executable}" -m {agy.HOOK_MODULE}')
    cli.main(["doctor", "--workspace", str(root), "--json"])
    report = json.loads(capsys.readouterr().out)
    hook = report["ai_access"]["antigravity_hook"]
    assert hook["verified"] and any("-I" in action and "Antigravity" in action for action in report["next_actions"])
    cli.main(["doctor", "--workspace", str(root)])
    assert "Antigravity hook: NOT IN FORCE" in capsys.readouterr().out


def name_host(root, host):
    path = Path(root) / "workspace.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**config, "host": host}), encoding="utf-8")


def doctor(capsys, root, *options):
    code = cli.main(["doctor", "--workspace", str(root), *options])
    return code, capsys.readouterr().out


# What a registered hook that blocks the probe, runs isolated and sees every tool reports.
WORKING = {"configured": True, "commands": ["python -I -m torque.gate_antigravity"], "matcher_covers_tools": True,
           "isolated": True, "disabled": False, "verified": True, "probe_decision": "deny", "probe_error": "",
           "recommended_command": "python -I -m torque.gate_antigravity", "recommended_entry": {}}


def test_doctor_requires_the_hook_of_the_host_the_workspace_names(tmp_path, capsys, monkeypatch):
    from torque import workspace as ws
    root = ws.init_workspace(tmp_path / "firm", "Example firm")
    ws.set_ai_access(root, "build-only")
    name_host(root, "antigravity")
    # No Antigravity hook: not ready. The missing Claude Code hook is only advice.
    code, out = doctor(capsys, root, "--json")
    report = json.loads(out)
    assert code == 3 and not report["ready"] and report["ai_access"]["host"] == "antigravity"
    assert any("names Antigravity" in action and agy.HOOK_MODULE in action for action in report["next_actions"])
    assert any("If Claude Code is used here" in action for action in report["next_actions"])
    assert not any("so nothing is blocked" in action for action in report["next_actions"])
    code, out = doctor(capsys, root)
    assert "AI access: build-only, host Antigravity (HOOK NOT IN FORCE)" in out
    assert "Claude Code hook: not wired in .claude/settings.json" in out

    # A working Antigravity hook makes it ready although Claude Code still has none.
    monkeypatch.setattr(cli, "_antigravity_hook_report", lambda root, mode: dict(WORKING))
    code, out = doctor(capsys, root, "--json")
    report = json.loads(out)
    assert code == 0 and report["ready"], report["next_actions"]
    assert any("If Claude Code is used here" in action for action in report["next_actions"])
    code, out = doctor(capsys, root)
    assert code == 0 and "host Antigravity (hook command blocked a standalone probe" in out

    # The same workspace without the host key is Claude Code's: its missing hook decides, as before.
    config = json.loads((Path(root) / "workspace.json").read_text(encoding="utf-8"))
    del config["host"]
    (Path(root) / "workspace.json").write_text(json.dumps(config), encoding="utf-8")
    code, out = doctor(capsys, root, "--json")
    report = json.loads(out)
    assert code == 3 and report["ai_access"]["host"] == "claude"
    assert any("so nothing is blocked" in action for action in report["next_actions"])


def test_doctor_treats_a_faulty_claude_code_hook_as_advice_under_antigravity(tmp_path, capsys, monkeypatch):
    from torque import workspace as ws
    root = ws.init_workspace(tmp_path / "firm", "Example firm")
    ws.set_ai_access(root, "build-only")
    name_host(root, "antigravity")
    monkeypatch.setattr(cli, "_antigravity_hook_report", lambda root, mode: dict(WORKING))
    broken = f'"{Path(sys.executable).as_posix()}" -c "import sys; sys.exit(1)" # torque.gate'
    settings = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": broken}]}]}}
    (Path(root) / ".claude" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    code, out = doctor(capsys, root, "--json")
    report = json.loads(out)
    assert code == 0 and report["ready"], report["next_actions"]
    advice = [action for action in report["next_actions"] if action.startswith("Claude Code (not this workspace")]
    assert len(advice) >= 2, report["next_actions"]
    code, out = doctor(capsys, root)
    assert "Claude Code hook: NOT IN FORCE" in out


def test_doctor_does_not_call_a_connected_antigravity_workspace_ready(tmp_path, capsys, monkeypatch):
    from torque import workspace as ws
    from torque.presence import Presence
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    root = ws.init_workspace(tmp_path / "firm", "Example firm")
    ws.set_ai_access(root, "connected", approval="required", presence=lambda: Presence(True, ""))
    name_host(root, "antigravity")
    # Whatever the Claude Code checks say, nothing here checked an Antigravity session.
    monkeypatch.setattr("torque.doctor_connected.report", lambda *a, **k: {
        "ready": True, "problems": [], "advice": [], "probes": [], "checks": [], "approval_verify": "hmac",
        "client": None, "profile": "interactive", "delegates": {}, "setup_steps": [], "approvals_by_kind": None,
        "approval_history": None})
    code, out = doctor(capsys, root, "--json")
    report = json.loads(out)
    assert code == 3 and not report["ready"]
    assert any("checks connected mode for Claude Code only" in action for action in report["next_actions"])
    assert any(agy.HOOK_MODULE in action for action in report["next_actions"])
    code, out = doctor(capsys, root)
    assert code == 3 and "NOT CHECKED under Antigravity" in out


def test_doctor_refuses_a_host_it_does_not_know(tmp_path, capsys):
    from torque import workspace as ws
    root = ws.init_workspace(tmp_path / "firm", "Example firm")
    name_host(root, "another-agent")
    assert cli.main(["doctor", "--workspace", str(root), "--json"]) == 2
    assert "unknown host" in capsys.readouterr().err


def test_the_documented_hook_is_the_one_the_code_produces():
    text = (Path(__file__).resolve().parents[1] / "docs" / "ai-access.md").read_text(encoding="utf-8")
    assert f"-I -m {agy.HOOK_MODULE}" in text and '"matcher": "*"' in text
    assert gate.SETTINGS_RE.search(".agents/hooks.json")
