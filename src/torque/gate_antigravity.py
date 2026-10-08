"""The gate as an Antigravity (`agy`) hook.

Antigravity hands a hook its own description of a tool call and reads its own
answer; the gate reads Claude Code's. This module turns one into the other and
runs the same gate, so a build-only workspace refuses in an Antigravity session
what it refuses in a Claude Code one.

Register it in the workspace's `.agents/hooks.json` (see `hook_entry`):

  {"torque-gate": {"PreToolUse": [{"matcher": "*", "hooks": [
      {"type": "command", "command": "PYTHON -I -m torque.gate_antigravity", "timeout": 30}]}]}}

Antigravity blocks a call when its hook crashes, runs past its timeout or prints
no decision, so a missing interpreter or a failed import blocks too.

Antigravity has no answer that means "no opinion". A call the gate has no
objection to is answered "allow" only when it is a read inside the folders the
session was started with, which Antigravity runs without asking anyway, and
"ask" otherwise, which hands it to Antigravity's own permission flow. The gate
never widens what that flow allows.

Connected mode is not supported here: `torque launch` starts Claude Code, so an
Antigravity session is not bound to a client and the gate treats it as unbound.

Tool names and arguments as observed with Antigravity CLI 1.3.1.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import sys
import threading

from . import gate

HOOK_NAME = "torque-gate"
HOOK_MODULE = "torque.gate_antigravity"
# Antigravity's default; the gate ends itself well inside it (gate.GATE_TIME_BUDGET).
HOOK_TIMEOUT = 30
# Antigravity runs commands in PowerShell on Windows and in the user's shell elsewhere.
SHELL_TOOL = "PowerShell" if os.name == "nt" else "Bash"
# Antigravity tool -> (the gate's tool, its path argument, where Antigravity puts the path).
FILE_TOOLS = {"view_file": ("Read", "file_path", ("AbsolutePath",)),
              "list_dir": ("LS", "path", ("DirectoryPath",)),
              "write_to_file": ("Write", "file_path", ("TargetFile",)),
              "replace_file_content": ("Edit", "file_path", ("TargetFile",)),
              "multi_replace_file_content": ("MultiEdit", "file_path", ("TargetFile",)),
              "sed_file": ("Edit", "file_path", ("TargetFile", "AbsolutePath")),
              "notebook_edit": ("NotebookEdit", "notebook_path", ("TargetFile", "AbsolutePath"))}
# Antigravity tool -> (the gate's tool, the argument with the pattern, the argument with the folder).
SEARCH_TOOLS = {"grep_search": ("Grep", "Query", "SearchPath"),
                "find_by_name": ("Glob", "Pattern", "SearchDirectory")}
# Text typed into a command that is already running is a command line too.
INPUT_TOOLS = ("manage_task", "send_command_input")
# Tools that name no file, run no command and reach no network: nothing for the
# gate to check. A worker started by one of them sends its own calls through this hook.
PLAIN_TOOLS = frozenset({
    "ask_custom_permission", "ask_permission", "ask_question", "browser_subagent", "command_status",
    "define_subagent", "finish", "invoke_subagent", "list_permissions", "list_plugin_accounts", "list_resources",
    "manage_subagents", "manage_task", "run_workflow", "schedule", "search_marketplace", "send_command_input",
    "send_message", "wait", "wait_5_seconds"})
# Network reads: nothing for the gate to check, and Antigravity's own flow decides.
NETWORK_TOOLS = frozenset({"read_url_content", "search_web"})
# Tools whose arguments can name a file or a page: every string argument is
# checked the way an MCP tool's is.
SCANNED_TOOLS = frozenset({
    "browser_click_element", "browser_drag_pixel_to_pixel", "browser_get_dom", "browser_get_network_request",
    "browser_input", "browser_list_network_requests", "browser_mouse_down", "browser_mouse_up",
    "browser_move_mouse", "browser_press_key", "browser_refresh_page", "browser_resize_window", "browser_scroll",
    "browser_scroll_dom", "browser_select_option", "capture_browser_console_logs", "capture_browser_screenshot",
    "click_browser_pixel", "delete_knowledge", "execute_browser_javascript", "generate_image",
    "list_browser_pages", "manage_inbox", "open_browser_url", "read_browser_page", "read_resource"})
READ_ONLY = ("view_file", "list_dir", "grep_search", "find_by_name")
_PATH_ARGUMENTS = ("AbsolutePath", "DirectoryPath", "SearchPath", "SearchDirectory")
# The gate names Claude Code's tools when it suggests what to use instead.
_TOOL_HINT = ("Use Bash, Read, Edit, Write, Grep or Glob",
              "Use run_command, view_file, replace_file_content, write_to_file, grep_search or find_by_name")


def hook_command(python: str) -> str:
    """The hook command for an interpreter that has Torque installed. Antigravity
    runs it through `cmd /c` on Windows and `sh -c` elsewhere, from `.agents/`.
    -I keeps the working folder, PYTHONPATH and the user site off sys.path, so a
    torque/ folder written into the workspace is never imported."""
    return (f'"{python}"' if re.search(r"\s", python) else python) + f" -I -m {HOOK_MODULE}"


def hook_entry(python: str) -> dict:
    """The entry to merge into the workspace's `.agents/hooks.json`."""
    return {HOOK_NAME: {"PreToolUse": [{"matcher": "*", "hooks": [
        {"type": "command", "command": hook_command(python), "timeout": HOOK_TIMEOUT}]}]}}


def _folder(payload: dict) -> str:
    """The session's working folder: the first workspace path, else the folder
    above `.agents`, where Antigravity starts the hook."""
    paths = payload.get("workspacePaths")
    if isinstance(paths, list) and paths and isinstance(paths[0], str) and paths[0]:
        return paths[0]
    here = Path.cwd()
    return str(here.parent if here.name == ".agents" else here)


def gate_event(payload: dict) -> dict:
    """The tool call in the form the gate reads. A tool this module does not
    know, or a known one without the argument that names its file, keeps its
    own name, which build-only mode blocks as a tool it does not recognise."""
    call = payload.get("toolCall")
    if not isinstance(call, dict) or not isinstance(call.get("name"), str) or not call["name"]:
        raise ValueError("the hook input has no tool name")
    name, args = call["name"], call.get("args")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ValueError("the tool arguments must be an object")
    folder = _folder(payload)
    cwd, tool, tool_input = folder, name, args
    if name == "run_command":
        if not isinstance(args.get("CommandLine"), str):
            raise ValueError("run_command has no CommandLine")
        tool, tool_input = SHELL_TOOL, {"command": args["CommandLine"]}
        if isinstance(args.get("Cwd"), str) and args["Cwd"]:
            cwd = os.path.join(folder, args["Cwd"])
    elif name in INPUT_TOOLS and isinstance(args.get("Input"), str):
        tool, tool_input = SHELL_TOOL, {"command": args["Input"]}
    elif name in FILE_TOOLS:
        gate_tool, key, sources = FILE_TOOLS[name]
        path = next((args[source] for source in sources if isinstance(args.get(source), str) and args[source]), None)
        if path is not None:
            tool, tool_input = gate_tool, {key: path}
        elif name == "list_dir":
            tool, tool_input = gate_tool, {key: folder}
    elif name in SEARCH_TOOLS:
        gate_tool, pattern, where = SEARCH_TOOLS[name]
        if isinstance(args.get(pattern), str):
            tool, tool_input = gate_tool, {"pattern": args[pattern]}
            if isinstance(args.get(where), str) and args[where]:
                tool_input["path"] = args[where]
            includes = args.get("Includes")
            if isinstance(includes, list) and all(isinstance(item, str) for item in includes):
                tool_input["glob"] = ",".join(includes)
    elif name == "call_mcp_tool":
        arguments = args.get("Arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                arguments = {"value": arguments}
        tool = f"mcp__{args.get('ServerName') or 'server'}__{args.get('ToolName') or 'tool'}"
        tool_input = arguments if isinstance(arguments, dict) else {"value": arguments}
    elif name in PLAIN_TOOLS or name in NETWORK_TOOLS:
        tool, tool_input = "Task", {}
    elif name in SCANNED_TOOLS:
        tool = "mcp__antigravity__" + name
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "cwd": cwd,
            "session_id": payload.get("conversationId")}


def _inside_session(payload: dict, args: dict) -> bool:
    """True when every folder or file the read names is inside a folder the
    session was started with (the working folder and those added to it)."""
    folder = _folder(payload)
    roots = [Path(os.path.realpath(root)) for root in payload.get("workspacePaths") or [folder]
             if isinstance(root, str) and root]
    named = [args[key] for key in _PATH_ARGUMENTS if isinstance(args.get(key), str) and args[key]]
    return bool(named) and all(
        any(gate._is_within(Path(os.path.realpath(os.path.join(folder, path))), root) for root in roots)
        for path in named)


def _no_objection(payload: dict) -> str:
    """Antigravity's decision for a call the gate lets through: "allow" only for
    a read inside the session's folders, which Antigravity runs without asking
    anyway; "ask" for the rest, so its own permission flow still decides."""
    call = payload["toolCall"]
    name, args = call["name"], call.get("args") or {}
    return "allow" if name in READ_ONLY and _inside_session(payload, args) else "ask"


def _explicit(text: str) -> tuple[str, str] | None:
    """Connected mode's own allow or ask, which the gate prints for Claude Code."""
    try:
        output = json.loads(text)["hookSpecificOutput"]
        decision = output["permissionDecision"]
    except (ValueError, KeyError, TypeError):
        return None
    if decision not in ("allow", "ask", "deny"):
        return None
    return decision, str(output.get("permissionDecisionReason") or "")


def _say(stream, decision: str, reason: str = "") -> int:
    stream.write(json.dumps({"decision": decision, "reason": reason}) + "\n")
    stream.flush()
    return 0


def _out_of_time(stream) -> None:
    """The hard end of the gate's time budget: block the call and end the process."""
    try:
        _say(stream, "deny", f"Torque gate: its {gate.GATE_TIME_BUDGET:g}-second time budget for one call ran "
                             "out; blocking to fail closed.")
    finally:
        os._exit(0)


def main() -> int:
    stdout = sys.stdout
    watchdog = threading.Timer(gate.GATE_TIME_BUDGET + gate.WATCHDOG_GRACE, _out_of_time, [stdout])
    watchdog.daemon = True
    watchdog.start()
    try:
        try:
            payload = json.loads(gate._hook_input())
            if not isinstance(payload, dict):
                raise ValueError("the hook input must be a JSON object")
            if "toolCall" not in payload:
                # An event with no tool call (after a call, around a model call, at the
                # end): nothing to decide. For a tool call this is no decision, a block.
                stdout.write("{}\n")
                return 0
            event = gate_event(payload)
            # The folder the session was started in binds it, as CLAUDE_PROJECT_DIR does
            # under Claude Code: leaving that folder does not end the gating.
            os.environ["CLAUDE_PROJECT_DIR"] = _folder(payload)
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = gate.evaluate(json.dumps(event))
        except Exception as exc:
            return _say(stdout, "deny", f"Torque gate: this call could not be read ({exc}); blocking to fail closed.")
        if code != 0:
            reason = err.getvalue().strip().replace(*_TOOL_HINT)
            return _say(stdout, "deny", reason or "Torque gate: this call is blocked.")
        explicit = _explicit(out.getvalue())
        if explicit:
            return _say(stdout, *explicit)
        return _say(stdout, _no_objection(payload))
    finally:
        watchdog.cancel()


if __name__ == "__main__":
    raise SystemExit(main())
