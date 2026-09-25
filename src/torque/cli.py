"""One entry point, with lazy adapters to the existing Salesforce workflow packages."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import importlib
from importlib import resources
import importlib.util
import inspect
import json
import os
import re
from pathlib import Path
import shutil
import sys
import subprocess

from . import __version__
from . import cli_approval
from . import delegation
from . import workspace as ws

DELEGATES = {
    "advisory": "jsc_advisory.cli",
    "qa": "jsc_qa.cli",
    "revert": "jsc_revert.cli",
    "logs": "jsc_loganalyzer.cli",
    "browser": "jsc_browser_tests.cli",
    "meeting": "meeting_processor.cli",
    "lesson": "jsc_memory.cli",
    "probes": "jsc_probes.cli",
    "ai-regression": "jsc_ai_prompt_regression.cli",
}
PUBLIC_ROUTES = {"deploy": ("revert", ["deploy"]), "data": ("revert", ["data"]),
                 "org": ("revert", ["org"]), "recover": ("revert", ["revert"])}
# Old path overrides must not carry one client's data directory into another client's scope.
STATE_OVERRIDES = (
    "JSC_MEMORY_ROOT", "JSC_MEMORY_DIR", "JSC_REVERT_DIR", "JSC_SESSION_SPOOL_DIR",
    "JSC_QA_SKIP_TOKEN_PATH", "JSC_DEPLOY_INTENT_TOKEN_PATH", "JSC_QA_ADV_PROBE_OUT",
    "TORQUE_QA_ROUTER", "TORQUE_TEST_USERS", "TORQUE_BROWSER_FLOWS", "TORQUE_BROWSER_REGISTRY",
    "TORQUE_AI_FIXTURES",
    "TORQUE_PARITY_SCRIPT", "TORQUE_BASELINE_ORG",
)


def _print_json(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def _client_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", required=True, help="private workspace directory")
    parser.add_argument("--client", required=True, help="explicit client name or slug")


def _disable_abbreviations(parser: argparse.ArgumentParser) -> None:
    """Accept only exact option names, in this parser and every subparser, so an
    abbreviation such as `--clie` is never read as `--client`."""
    parser.allow_abbrev = False
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                _disable_abbreviations(child)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="torque", description="Salesforce consulting workflows and private client context.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")
    work = sub.add_parser("workspace", help="initialize or update a private consulting workspace")
    work_sub = work.add_subparsers(dest="action", required=True)
    init = work_sub.add_parser("init", help="create local files; does not connect an org")
    init.add_argument("path")
    init.add_argument("--name", required=True)
    init.add_argument("--profile", choices=ws.PROFILES, default="generic")
    init.add_argument("--json", action="store_true")
    upgrade = work_sub.add_parser("upgrade", help="update packaged recipes while preserving local edits")
    upgrade.add_argument("path")
    upgrade.add_argument("--check", action="store_true", help="show available updates without writing")
    upgrade.add_argument("--json", action="store_true")
    ai_access = work_sub.add_parser("ai-access", help="set build-only or connected mode; the owner runs this, "
                                                      "not an AI session")
    ai_access.add_argument("mode", choices=ws.AI_ACCESS_MODES)
    ai_access.add_argument("--approval", choices=ws.APPROVAL_VALUES,
                           help="connected mode only: org writes need a per-write approval (required)")
    ai_access.add_argument("--verify", choices=ws.APPROVAL_VERIFY,
                           help="connected mode only: hmac (same OS account) or owner-uid (separate approver account)")
    ai_access.add_argument("--approver-uid", type=int, help="owner-uid verification: the approver account's uid")
    ai_access.add_argument("--path", default=".", help="workspace directory; defaults to the current directory")
    ai_access.add_argument("--delegated", action="store_true",
                           help="the workspace's setup delegate is running this, not the owner")
    ai_access.add_argument("--model-id", help="delegated only: the AI reviewer's model identifier")
    ai_access.add_argument("--json", action="store_true")
    delegate_p = work_sub.add_parser("delegate", help="name a delegated approver or setup delegate; the owner "
                                                       "at a real terminal, or an administrator provisioning "
                                                       "the workspace, runs this")
    delegate_p.add_argument("--path", default=".", help="workspace directory; defaults to the current directory")
    delegate_p.add_argument("--role", required=True, choices=delegation.ROLES)
    delegate_p.add_argument("--account", required=True, help="the delegate's OS account name")
    delegate_p.add_argument("--uid", required=True, type=int, help="the delegate account's numeric uid")
    delegate_p.add_argument("--kind", required=True, choices=delegation.KINDS)
    delegate_p.add_argument("--json", action="store_true")
    demo = sub.add_parser("demo", help="create an offline synthetic consulting workspace; no org needed")
    demo.add_argument("path")
    demo.add_argument("--json", action="store_true")
    client = sub.add_parser("client", help="manage a client's private context")
    client_sub = client.add_subparsers(dest="action", required=True)
    add = client_sub.add_parser("add")
    add.add_argument("name")
    add.add_argument("--workspace", required=True)
    add.add_argument("--org", help="record an alias only; no login or org call")
    add.add_argument("--json", action="store_true")
    clients = client_sub.add_parser("list")
    clients.add_argument("--workspace", required=True)
    clients.add_argument("--json", action="store_true")
    cli_approval.register_consent(client_sub)
    context = sub.add_parser("context", help="read only the selected client's context and recent sessions")
    _client_args(context)
    context.add_argument("--json", action="store_true")
    session = sub.add_parser("session", help="append or read user-reported session records")
    sessions = session.add_subparsers(dest="action", required=True)
    entry = sessions.add_parser("add")
    _client_args(entry)
    entry.add_argument("--summary", required=True)
    entry.add_argument("--status", choices=ws.STATUSES, default="prepared")
    entry.add_argument("--evidence", help="reference and hash a file; does not assess its contents")
    entry.add_argument("--json", action="store_true")
    for action in ("list", "show"):
        p = sessions.add_parser(action)
        _client_args(p)
        p.add_argument("--json", action="store_true")
        if action == "show":
            p.add_argument("entry_id")
    handoff = sub.add_parser("handoff", help="render a handoff from the actual selected-client journal")
    _client_args(handoff)
    handoff.add_argument("--output", help="write a new file; otherwise print Markdown")
    doctor = sub.add_parser("doctor", help="inspect local dependencies and config; makes no org calls")
    doctor.add_argument("--workspace")
    doctor.add_argument("--client")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--live", action="store_true",
                        help="connected mode: resolve each approved org and compare its ID with the consent")
    doctor.add_argument("--for", dest="capability", choices=("workspace", "salesforce", "browser", "meeting"), default="workspace")
    change = sub.add_parser("change", help="connect requirements, decisions, checks and handoff")
    actions = change.add_subparsers(dest="action", required=True)
    for action in ("create", "list", "show", "note", "check", "verify-deploy", "handoff"):
        cp = actions.add_parser(action)
        _client_args(cp)
        cp.add_argument("--json", action="store_true")
        if action not in ("create", "list"):
            cp.add_argument("change_id")
        if action == "create":
            cp.add_argument("--title", required=True)
            cp.add_argument("--outcome", required=True, help="the client's intended business result")
            cp.add_argument("--criterion", action="append", default=[])
            cp.add_argument("--org", help="record a planned target; no org call")
        elif action == "note":
            cp.add_argument("--text", required=True)
            cp.add_argument("--kind", choices=("note", "decision", "next_step"), default="note")
        elif action == "check":
            cp.add_argument("--criterion", required=True, help="AC1, AC2, etc.")
            cp.add_argument("--result", choices=("pass", "fail", "unknown", "not_run"), required=True)
            cp.add_argument("--summary", required=True)
            cp.add_argument("--evidence", help="capture a private evidence file; result remains operator-reported")
        elif action == "verify-deploy":
            cp.add_argument("--org", required=True)
            cp.add_argument("--job-id", required=True)
            cp.add_argument("--component", action="append", default=[])
            cp.add_argument("--manifest")
        elif action == "handoff":
            cp.add_argument("--output", help="write a new report file")
    workflows = sub.add_parser("workflows", help="discover bundled conversational and native workflows")
    workflows.add_argument("action", nargs="?", choices=("list", "show"), default="list")
    workflows.add_argument("name", nargs="?")
    workflows.add_argument("--json", action="store_true")
    workflows.add_argument("--workspace", help="prefer a selected workspace local recipe; otherwise show the packaged reference")
    cli_approval.register(sub)
    for route in PUBLIC_ROUTES:
        sub.add_parser(route, add_help=False, help=f"{route} operations with selected-client evidence; use {route} --help")
    for route in DELEGATES:
        sub.add_parser(route, add_help=False, help=f"forward to the {route} workflow; use {route} --help")
    _disable_abbreviations(parser)
    parser.epilog = ("Delegated workflows accept --workspace PATH --client NAME for isolated client state. "
                     "Use the delegated --help for its native arguments. No global hooks or sf replacement.")
    return parser


def _context_options(argv: list[str]) -> tuple[list[str], str | None, str | None]:
    remaining: list[str] = []
    values: dict[str, str] = {}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            remaining.extend(argv[i:])
            break
        option = arg.split("=", 1)[0]
        if option not in ("--workspace", "--client"):
            remaining.append(arg)
            i += 1
            continue
        if "=" in arg:
            value = arg.split("=", 1)[1]
        else:
            i += 1
            if i >= len(argv) or argv[i].startswith("--"):
                raise ws.WorkspaceError(f"{option} needs a value")
            value = argv[i]
        if not value.strip():
            raise ws.WorkspaceError(f"{option} needs a nonempty value")
        if option in values and values[option] != value:
            raise ws.WorkspaceError(f"conflicting {option} values")
        values[option] = value
        i += 1
    return remaining, values.get("--workspace"), values.get("--client")


@contextmanager
def delegated_context(path: Path | None, route: str, argv: list[str], display: str | None = None):
    previous_argv = sys.argv
    changes: dict[str, str | None] = {}
    if path is not None:
        changes = {"TORQUE_WORKSPACE": str(path), "JSC_ROOT": str(path)}
        changes.update({key: None for key in STATE_OVERRIDES})
        # Editable optional adapters belong to this client's explicit config directory.
        for key, filename in (("TORQUE_QA_ROUTER", "qa-router.yaml"),
                              ("TORQUE_TEST_USERS", "test-users.json"),
                              ("TORQUE_BROWSER_FLOWS", "browser-flows"),
                              ("TORQUE_BROWSER_REGISTRY", "object-registry.yaml"),
                              ("TORQUE_AI_FIXTURES", "ai-fixtures")):
            candidate = ws._inside(path, path / "config" / filename)
            directory_adapter = key in {"TORQUE_BROWSER_FLOWS", "TORQUE_AI_FIXTURES"}
            if candidate.is_dir() if directory_adapter else candidate.is_file():
                changes[key] = str(candidate)
        parity_config = ws._inside(path, path / "config" / "parity.json")
        if parity_config.is_file():
            parity = ws._read_json(parity_config)
            script, source_org = parity.get("script"), parity.get("baseline_org")
            if not isinstance(script, str) or not script or Path(script).is_absolute() or any(part in (".", "..") for part in script.split("/")):
                raise ws.WorkspaceError("config/parity.json script must be a relative file under this client's config directory")
            if not isinstance(source_org, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", source_org):
                raise ws.WorkspaceError("config/parity.json baseline_org must be a nonempty org alias")
            adapter = ws._inside(path, path / "config" / script)
            if not adapter.is_file():
                raise ws.WorkspaceError("config/parity.json script does not exist")
            changes["TORQUE_PARITY_SCRIPT"] = str(adapter)
            changes["TORQUE_BASELINE_ORG"] = source_org
    previous = {key: os.environ.get(key) for key in changes}
    try:
        for key, value in changes.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        sys.argv = [display or f"torque {route}", *argv]
        yield
    finally:
        sys.argv = previous_argv
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _dispatch(route: str, argv: list[str], display: str | None = None) -> int:
    args, root_arg, client_name = _context_options(argv)
    flags = args[:args.index("--")] if "--" in args else args
    help_only = not args or any(x in ("-h", "--help", "--version") for x in flags)
    scope = None
    # Legacy JSC_ROOT may still name the old employer checkout. It is an output compatibility
    # variable, never an implicit client selection for this entry point.
    root_arg = root_arg or os.environ.get("TORQUE_WORKSPACE")
    if client_name and not root_arg and not help_only:
        raise ws.WorkspaceError("--client requires --workspace PATH")
    if root_arg and not help_only:
        if client_name:
            scope, _, _ = ws.load_client(root_arg, client_name)
        elif (Path(root_arg).expanduser() / "client.json").is_file():
            # An inherited context is already client-scoped; validate it through its firm root.
            candidate = Path(root_arg).expanduser().resolve()
            scope, _, _ = ws.load_client(candidate.parent.parent, candidate.name)
        else:
            ws.load_workspace(root_arg)
    stateful = route in {"qa", "revert", "lesson", "browser"}
    stateful = stateful or (route == "ai-regression" and args[:1] in (["replay"], ["replay-all"]))
    local_browser = route == "browser" and args[:1] == ["sanitize-replay"]
    if not help_only and stateful and not local_browser and scope is None:
        raise ws.WorkspaceError(f"{route} needs a selected client for its private state; "
                                "provide --workspace PATH --client NAME")
    if not help_only and route == "meeting" and not any(a == "--output" or a.startswith("--output=") for a in args):
        if scope is None:
            raise ws.WorkspaceError("meeting needs --output PATH or --workspace PATH --client NAME")
        from datetime import datetime, timezone
        output = scope / "artifacts" / "meetings" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        ws._inside(scope, output)
        args += ["--output", str(output)]
    with delegated_context(scope, route, args, display):
        try:
            module = importlib.import_module(DELEGATES[route])
        except ModuleNotFoundError as exc:
            raise ws.WorkspaceError(f"{route} dependency is unavailable ({exc.name}). "
                                    "Install Torque's packaged workflows and the relevant optional extra; "
                                    "see the installation guide.") from exc
        parameters = inspect.signature(module.main).parameters
        result = module.main(argv=args) if "argv" in parameters else module.main()
        return result if isinstance(result, int) else 0


def _data_text(relative: str) -> str | None:
    candidates = (f"data/workflows/{relative}", f"data/{relative}", f"data/commands/{relative}")
    for name in candidates:
        try:
            item = resources.files("torque").joinpath(name)
            if item.is_file():
                return item.read_text(encoding="utf-8")
        except (OSError, TypeError, ModuleNotFoundError):
            pass
    root = Path(__file__).resolve().parents[2]
    candidate = root / "workflows" / relative
    return candidate.read_text(encoding="utf-8") if candidate.is_file() else None


def _workflows(args: argparse.Namespace) -> int:
    raw = _data_text("catalogue.json")
    if raw is None:
        raise ws.WorkspaceError("workflow catalogue is not installed; reinstall the complete Torque package")
    try:
        entries = json.loads(raw)
        if isinstance(entries, dict):
            entries = entries.get("workflows", [])
        if not isinstance(entries, list) or any(not isinstance(e, dict) or not e.get("name") for e in entries):
            raise ValueError("invalid workflow entries")
    except ValueError as exc:
        raise ws.WorkspaceError("installed workflow catalogue is invalid") from exc
    if args.action == "show":
        if not args.name:
            raise ws.WorkspaceError("workflows show needs a workflow name")
        entry = next((e for e in entries if e["name"] == args.name), None)
        if entry is None:
            raise ws.WorkspaceError(f"unknown workflow: {args.name}; run torque workflows")
        selected = getattr(args, "workspace", None) or os.environ.get("TORQUE_WORKSPACE")
        workspace = None
        if selected:
            candidate = Path(selected).expanduser().resolve()
            if (candidate / "client.json").is_file():
                client, _, _ = ws.load_client(candidate.parent.parent, candidate.name)
                workspace = client.parent.parent
            else:
                workspace, _ = ws.load_workspace(candidate)
        if args.json:
            # Catalogue JSON remains stable; workspace precedence selects text.
            _print_json(entry)
        else:
            recipe = None
            if workspace is not None:
                local = ws._inside(workspace, workspace / ".claude" / "commands" / f"{ws.slug_for(args.name)}.md")
                if local.exists():
                    if not local.is_file():
                        raise ws.WorkspaceError("selected local workflow must be a regular Markdown file")
                    recipe = local.read_text(encoding="utf-8")
            if recipe is None:
                recipe = _data_text(f"{ws.slug_for(args.name)}.md")
            print(recipe if recipe is not None else json.dumps(entry, indent=2), end="\n")
    elif args.json:
        _print_json(entries)
    else:
        for entry in entries:
            print(f"{entry['name']:22} [{entry.get('kind', 'guided')}] {entry.get('description', '')}")
        print("\nRead a workflow: torque workflows show NAME")
    return 0


def _change(args: argparse.Namespace) -> int:
    from . import changes
    common = (args.workspace, args.client)
    action = args.action
    if action == "create":
        result = changes.create_change(*common, args.title, args.outcome, args.criterion, args.org)
    elif action == "list":
        result = changes.list_changes(*common)
    elif action in ("show", "handoff"):
        result = changes.get_change(*common, args.change_id)
        if not args.json:
            text = changes.render_change(*common, args.change_id)
            if action == "handoff" and args.output:
                ws.atomic_write_new(ws.client_output_path(*common, args.output), text)
                print(args.output)
            else:
                print(text, end="")
            return 0
        if action == "handoff" and args.output:
            ws.atomic_write_new(ws.client_output_path(*common, args.output), json.dumps(result, indent=2) + "\n")
    elif action == "note":
        result = changes.add_note(*common, args.change_id, args.text, args.kind)
    elif action == "check":
        result = changes.add_check(*common, args.change_id, args.criterion, args.result, args.summary, args.evidence)
    else:
        result = changes.verify_deploy(*common, args.change_id, args.org, args.job_id, args.component, args.manifest)
    if args.json:
        _print_json(result)
    elif action == "list":
        for item in result:
            print(f"{item['id']}: {item['title']}")
        if not result:
            print("No changes yet. Use torque change create --help.")
    else:
        print(f"Recorded {result['id']}: {result.get('summary', result.get('title', ''))}")
    return 3 if action == "verify-deploy" and result.get("result") != "pass" else 0


# The gate blocks tools it does not recognise, so the hook must see every tool
# call. A matcher is a regular expression over the tool name ("" and "*" match
# all); it covers the tools when it matches each of these, including a made-up
# name standing for tools a host adds later.
_HOOK_COVERAGE_PROBES = ("Bash", "Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "NotebookRead", "LS",
                         "Grep", "Glob", "Monitor", "PowerShell", "WebFetch", "Task", "mcp__server__tool",
                         "FutureTool")


def _matcher_covers(matchers: list[str]) -> bool:
    """True when the union of the hook entries' matchers matches every probe name."""
    def matches(matcher: str, name: str) -> bool:
        if matcher.strip() in ("", "*"):
            return True
        try:
            return re.fullmatch(matcher, name) is not None
        except re.error:
            return False
    return bool(matchers) and all(any(matches(m, name) for m in matchers) for name in _HOOK_COVERAGE_PROBES)


def _hook_settings_layers(root: Path) -> list[Path]:
    """Claude Code settings files that can switch the workspace's hooks off:
    the workspace's own, the user's, and the managed (administrator) file."""
    layers = [root / ".claude" / "settings.json", root / ".claude" / "settings.local.json",
              Path.home() / ".claude" / "settings.json"]
    if sys.platform == "darwin":
        layers.append(Path("/Library/Application Support/ClaudeCode/managed-settings.json"))
    elif os.name == "nt":
        layers.append(Path(os.environ.get("ProgramData") or "C:/ProgramData") / "ClaudeCode" / "managed-settings.json")
    else:
        layers.append(Path("/etc/claude-code/managed-settings.json"))
    return layers


def _hooks_disabled_by(root: Path) -> list[str]:
    found = []
    layers = _hook_settings_layers(root)
    for path in layers:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if data.get("disableAllHooks") is True:
            found.append(f"{path} (disableAllHooks)")
        if path == layers[-1] and data.get("allowManagedHooksOnly") is True:
            found.append(f"{path} (allowManagedHooksOnly)")
    return found
# The interpreter path, then an option group containing I (isolated mode).
_HOOK_ISOLATED_RE = re.compile(r'^\s*(?:"[^"]*"|\S+)\s+(?:-[A-Za-z]+\s+)*-[A-Za-z]*I[A-Za-z]*\s')


def _hook_shell() -> str | None:
    """On Windows, the Git Bash that Claude Code runs hooks through, or None.
    Elsewhere None: the probe runs through the default POSIX shell."""
    if os.name != "nt":
        return None
    configured = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH")
    if configured and Path(configured).is_file():
        return configured
    candidates = []
    git = shutil.which("git")
    if git:
        git_dir = Path(git).resolve().parent
        candidates += [git_dir.parent / "bin" / "bash.exe", git_dir.parent.parent / "bin" / "bash.exe"]
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432"), os.environ.get("LOCALAPPDATA")):
        if base:
            candidates += [Path(base) / "Git" / "bin" / "bash.exe", Path(base) / "Programs" / "Git" / "bin" / "bash.exe"]
    return next((str(c) for c in candidates if c.is_file()), None)


def _run_hook_probe(command: str, root: Path, event: str) -> tuple[int | None, str]:
    shell = _hook_shell()
    try:
        if shell:
            run = subprocess.run([shell, "-c", command], cwd=root, input=event, capture_output=True,
                                 text=True, timeout=60)
        else:
            run = subprocess.run(command, shell=True, cwd=root, input=event, capture_output=True,
                                 text=True, timeout=60)
        return run.returncode, run.stderr
    except (OSError, subprocess.TimeoutExpired):
        return None, ""




def _gate_hook_report(root: Path) -> dict:
    """Inspect the workspace's own Claude Code hook for build-only mode and,
    in build-only mode, run it once on a synthetic client-path Read to prove it
    blocks. Makes no org call and reads no client file."""
    from . import gate
    mode_root, mode, known = gate._workspace_mode(root)
    python = sys.executable.replace("\\", "/")
    hook: dict = {"configured": False, "commands": [], "matcher_covers_tools": False,
                  "fail_closed_shim": False, "isolated": False, "disabled_by": [], "verified": None,
                  "probe_exit": None, "timeouts": [],
                  "probe_error": "", "recommended_command": gate.hook_command(python)}
    matchers: list[str] = []
    for name in ("settings.json", "settings.local.json"):
        path = root / ".claude" / name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        hooks = data.get("hooks") if isinstance(data, dict) else None
        entries = hooks.get("PreToolUse") if isinstance(hooks, dict) else None
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            matcher = str(entry.get("matcher") or "")
            for item in entry.get("hooks") or []:
                command = str(item.get("command") or "") if isinstance(item, dict) else ""
                if "torque.gate" in command:
                    hook["commands"].append(command)
                    hook["timeouts"].append(item.get("timeout"))
                    hook["configured"] = True
                    matchers.append(matcher)
    hook["matcher_covers_tools"] = _matcher_covers(matchers)
    hook["disabled_by"] = _hooks_disabled_by(root)
    hook["fail_closed_shim"] = bool(hook["commands"]) and all("sys.excepthook" in c for c in hook["commands"])
    hook["isolated"] = bool(hook["commands"]) and all(_HOOK_ISOLATED_RE.match(c) for c in hook["commands"])
    if mode == "build-only":
        hook["verified"] = False
        if hook["commands"]:
            event = json.dumps({"tool_name": "Read", "cwd": str(root),
                                "tool_input": {"file_path": "clients/.torque-doctor-probe/probe.md"}})
            results = [_run_hook_probe(command, root, event) for command in hook["commands"]]
            hook["probe_exit"] = results[0][0]
            hook["verified"] = all(code == 2 and "could not" not in err for code, err in results)
            failed = [err for code, err in results if code == 2 and "could not" in err]
            if failed:
                hook["probe_error"] = (failed[0].strip().splitlines() or [""])[-1][:300]
    return {"mode": mode, "mode_known": known, "governing_workspace": str(mode_root), "hook": hook}


# The hook timeout the documentation gives, in seconds (Claude Code's default).
HOOK_TIMEOUT = 600
# The most entries doctor's link scan reads before it stops and reports an
# incomplete scan.
LINK_SCAN_LIMIT = 200_000
_LINK_SCAN_SKIP = {"node_modules", ".git"}


def _links_out(root: Path) -> dict:
    """Symbolic links (and Windows junctions) in the workspace, outside clients/
    and skipping node_modules and .git folders, that lead to clients/, into it,
    to the workspace root or to a folder above it. Each link is resolved fully
    (realpath). A link to a folder outside the workspace is followed there too,
    so one that reaches clients/ through an outside folder is reported as
    `path (via outside-link)`. A recursive tool that follows links (rg -L,
    grep -R, find -L, macOS cp -r, ...) would read clients/ through any of
    them. Run once by doctor; the gate itself does not walk the tree."""
    from . import gate
    root = Path(os.path.realpath(str(root)))
    clients = Path(os.path.realpath(str(root / "clients")))
    found: list[str] = []
    budget = LINK_SCAN_LIMIT
    queue: list[tuple[Path, str | None]] = [(root, None)]
    scanned: set[str] = set()
    while queue:
        start, origin = queue.pop(0)
        key = gate._cf(str(start))
        if key in scanned:
            continue
        scanned.add(key)
        for folder, dirs, files in os.walk(start):
            here = Path(folder)
            budget -= 1 + len(dirs) + len(files)
            if budget < 0:
                return {"found": sorted(found), "complete": False}
            keep = []
            for name in dirs:
                path = here / name
                if name in _LINK_SCAN_SKIP or (here == root and name == "clients"):
                    continue
                if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                    files.append(name)
                    continue
                keep.append(name)
            dirs[:] = keep
            for name in files:
                path = here / name
                if not (path.is_symlink() or getattr(path, "is_junction", lambda: False)()):
                    continue
                real = Path(os.path.realpath(str(path)))
                label = origin or path.relative_to(root).as_posix()
                if origin is not None:
                    label = f"{origin} (via {path.as_posix()})"
                if gate._reaches(real, clients):
                    found.append(label)
                elif real.is_dir() and not gate._is_within(real, root):
                    queue.append((real, origin or path.relative_to(root).as_posix()))
    return {"found": sorted(dict.fromkeys(found)), "complete": True}


def _doctor(args: argparse.Namespace) -> int:
    if args.client and not args.workspace:
        raise ws.WorkspaceError("doctor --client requires --workspace")
    report = {"python": sys.version.split()[0], "torque_version": __version__,
              "installation": {"package": str(Path(__file__).resolve().parent),
                               "python_executable": sys.executable},
              "executables": {name: shutil.which(name) for name in ("sf", "git", "ffmpeg")},
              "optional_modules": {name: importlib.util.find_spec(name) is not None
                                   for name in ("yaml", "playwright", "PIL")},
              "org_calls": False, "workspace": None, "client": None}
    if args.workspace:
        root, firm = ws.load_workspace(args.workspace)
        report["workspace"] = {"path": str(root), "name": firm["name"], "profile": firm["profile"]}
        report["ai_access"] = _gate_hook_report(Path(root))
        if args.client:
            client, _, data = ws.load_client(root, args.client)
            # Reading the selected journal also checks its local format, without evaluating claims.
            entries = ws.list_sessions(root, args.client, limit=None)
            from .changes import get_change, list_changes
            changes = [get_change(root, args.client, change["id"])
                       for change in list_changes(root, args.client)]
            evidence_problems = sum(entry["evidence_integrity"] in ("missing", "changed", "unavailable")
                                    for entry in entries)
            evidence_problems += sum(change["assessment"]["evidence_problems"] for change in changes)
            report["client"] = {"path": str(client), "name": data["name"],
                                "recent_sessions": min(len(entries), 20), "sessions_checked": len(entries),
                                "changes_checked": len(changes), "evidence_problems": evidence_problems}
    requirements = {"workspace": (), "salesforce": ("sf",), "browser": ("sf", "playwright"),
                    "meeting": ("ffmpeg", "PIL")}
    available = {**{name: bool(path) for name, path in report["executables"].items()}, **report["optional_modules"]}
    report["capabilities"] = {name: {"local_dependencies_ready": all(available.get(dep, False) for dep in deps),
                                            "missing": [dep for dep in deps if not available.get(dep, False)],
                                            "live_verified": False}
                              for name, deps in requirements.items()}
    selected = getattr(args, "capability", "workspace")
    report["requested_capability"] = selected
    report["ready"] = report["capabilities"][selected]["local_dependencies_ready"]
    report["next_actions"] = []
    access = report.get("ai_access")
    if access and access["mode"] == "build-only":
        hook = access["hook"]
        if not hook["verified"]:
            report["ready"] = False
            if not hook["configured"]:
                report["next_actions"].append(
                    "Build-only mode is set but no torque.gate hook is wired in this workspace's "
                    ".claude/settings.json, so nothing is blocked. Add the hook from docs/ai-access.md "
                    "with this command: " + hook["recommended_command"])
            elif hook["probe_error"]:
                report["next_actions"].append(
                    "The torque.gate hook could not run the gate (" + hook["probe_error"] + "), so it "
                    "blocks every tool call, ordinary work included. Point the hook at an interpreter "
                    "with Torque installed, using: " + hook["recommended_command"])
            else:
                report["next_actions"].append(
                    f"The torque.gate hook did not block a client-path probe (exit {hook['probe_exit']}), "
                    "so build-only mode is not in force. Point the hook at an interpreter with Torque "
                    "installed, using: " + hook["recommended_command"])
        else:
            if not hook["fail_closed_shim"]:
                report["next_actions"].append(
                    "The torque.gate hook works, but fails open if its interpreter later loses Torque. "
                    "Switch to the fail-closed hook command: " + hook["recommended_command"])
            if not hook["isolated"]:
                report["ready"] = False
                report["next_actions"].append(
                    "The torque.gate hook runs Python without -I (isolated mode), so a torque/ folder or "
                    "sitecustomize.py written into the workspace can replace the gate. Switch to: "
                    + hook["recommended_command"])
        slow = [t for t in hook["timeouts"] if not isinstance(t, (int, float)) or isinstance(t, bool)
                or t > HOOK_TIMEOUT]
        if hook["configured"] and slow:
            from . import gate
            report["next_actions"].append(
                f'Set "timeout": {HOOK_TIMEOUT} on the torque.gate hook entry (it has '
                + ("none" if slow[0] is None else repr(slow[0])) + "). A hook that times out lets the call "
                f"proceed; the gate blocks by itself once its {gate.GATE_TIME_BUDGET:g}-second budget runs out, "
                "well inside that timeout.")
        links = _links_out(Path(root))
        access["links_out"] = links
        if links["found"]:
            report["ready"] = False
            shown = ", ".join(links["found"][:10]) + (" ..." if len(links["found"]) > 10 else "")
            report["next_actions"].append(
                f"{len(links['found'])} link(s) in the workspace lead to clients/ or above it: {shown}. A recursive "
                "tool that follows links (rg -L, grep -R, find -L, macOS cp -r and others) would read client "
                "files through them, and the gate does not walk the tree. Remove or repoint them.")
        elif not links["complete"]:
            report["ready"] = False
            report["next_actions"].append(
                f"The link scan stopped after {LINK_SCAN_LIMIT} entries (node_modules and .git are skipped), so "
                "links that lead to clients/ or above it may remain. Check large folders for such links.")
        from . import gate
        count = gate.clients_index_count(root)
        access["clients_in_git"] = "unknown" if count is None else count
        if count is None:
            report["ready"] = False
            report["next_actions"].append(
                "Torque could not check whether files under clients/ are tracked in Git (git failed, timed out "
                "or is missing), so the gate blocks every git command here except git status without "
                "-v/--verbose and git rm --cached of paths under clients/. Check that git works in this workspace.")
        elif count:
            report["ready"] = False
            report["next_actions"].append(
                f"{access['clients_in_git']} file(s) under clients/ are tracked in Git or staged. Client files "
                "must stay untracked: low-level git commands can read them, so the gate blocks every git "
                "command here except git status without -v/--verbose and git rm --cached of paths under "
                "clients/. Run git rm -r --cached clients and keep clients/ ignored.")
        if hook["disabled_by"]:
            report["ready"] = False
            report["next_actions"].append(
                "Claude Code will not run this workspace's hook: " + "; ".join(hook["disabled_by"])
                + ". Remove disableAllHooks (or allowManagedHooksOnly) so the torque.gate hook runs.")
        if hook["configured"] and not hook["matcher_covers_tools"]:
            report["ready"] = False
            report["next_actions"].append(
                'The hook matcher does not cover every tool call. Set it to ".*": the gate checks '
                "command-running tools such as Monitor and blocks tools it does not recognise, but only "
                "for the calls the matcher sends it.")
    if access and access["mode"] == "connected":
        from . import doctor_connected
        connected = doctor_connected.report(Path(root), args.client, live=getattr(args, "live", False))
        access["connected"] = connected
        if not connected["ready"]:
            report["ready"] = False
        report["next_actions"] += [f"Connected mode: {problem.rstrip('.')}." for problem in connected["problems"]]
        report["next_actions"] += [f"Connected mode (advice): {note.rstrip('.')}." for note in connected["advice"]]
    if report["client"] and report["client"]["evidence_problems"]:
        report["next_actions"].append(
            f"Review {report['client']['evidence_problems']} missing, changed or unavailable evidence references "
            "in this client's handoff before relying on the recorded checks.")
    if not available["sf"]:
        report["next_actions"].append("Install the official Salesforce CLI before live org work; context and the offline demo remain usable.")
    if not available["playwright"]:
        report["next_actions"].append("Browser testing needs Torque's browser extra and a configured browser runtime. See docs/installation.md.")
    if args.workspace:
        private_paths = ["workspace.json", "profile.md", ".torque", f"clients/{ws.slug_for(args.client)}" if args.client else "clients"]
        try:
            from . import gate
            tracked = gate._git_run(root, ["ls-files", "--", *private_paths])
            ok = tracked is not None and tracked[0] == 0
            report["git_tracking"] = {"checked": ok, "tracked_private_paths": tracked[1].splitlines() if ok else []}
            if report["git_tracking"]["tracked_private_paths"]:
                report["next_actions"].append("Some private paths are already tracked by Git. Ignore rules do not untrack existing files; review their repository visibility.")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            report["git_tracking"] = {"checked": False, "tracked_private_paths": []}
    if args.json:
        _print_json(report)
    else:
        print(f"Torque {__version__}; Python {report['python']}")
        print(f"Installation: {report['installation']['package']}")
        for name, path in report["executables"].items():
            print(f"{name}: {path or 'not on PATH'}")
        for name, found in report["optional_modules"].items():
            print(f"{name}: {'available' if found else 'not installed'}")
        if report["workspace"]:
            print(f"Workspace: {report['workspace']['name']}")
            access = report["ai_access"]
            if access["mode"] == "build-only":
                h = access["hook"]
                ok = h["verified"] and h["isolated"] and h["matcher_covers_tools"] and not h["disabled_by"]
                state = ("hook command blocked a standalone probe; settings checked, host enforcement "
                         "not tested" if ok else "HOOK NOT IN FORCE")
                print(f"AI access: build-only ({state})")
            elif access["mode"] == "connected":
                connected = access["connected"]
                state = "ready" if connected["ready"] else "NOT READY"
                print(f"AI access: connected, approval required ({connected['approval_verify']}; {state})")
                from . import doctor_connected
                doctor_connected.print_report(connected)
            else:
                print("AI access: full (build-only mode off)")
        if report["client"]:
            print(f"Client: {report['client']['name']}")
        print(f"{selected}: {'local dependencies ready' if report['ready'] else 'missing local dependencies'}")
        for action in report["next_actions"]:
            print(f"Next: {action}")
        print("Local inspection only. No org authentication, API calls or runtime tests were performed.")
    return 0 if report["ready"] else 3


def _recover_text(text: str) -> str:
    """Rewrite the delegate's `revert <show|preview|exec|discard>` grammar into the
    public `torque recover <show|preview|run|discard>` grammar, keeping argparse's
    usage continuation lines aligned under the shorter prog."""
    out: list[str] = []
    indent_shift = 0
    for line in text.split("\n"):
        for old, new in (("torque recover revert exec", "torque recover run"),
                         ("torque recover revert", "torque recover")):
            if old in line:
                if line.startswith("usage: "):
                    indent_shift = len(old) - len(new)
                line = line.replace(old, new)
                break
        else:
            if indent_shift and line.startswith(" " * (len("usage: ") + indent_shift)):
                line = line[indent_shift:]
            elif not line.strip():
                indent_shift = 0
        line = line.replace("{show,preview,exec,discard}", "{show,preview,run,discard}")
        line = line.replace("'exec'", "'run'")
        line = re.sub(r"^(\s+)exec(\s{2,})", lambda m: f"{m.group(1)}run {m.group(2)}", line)
        out.append(line)
    return "\n".join(out)


@contextmanager
def _recover_grammar():
    original = argparse.ArgumentParser._print_message

    def _print_message(self, message, file=None):
        return original(self, _recover_text(message) if message else message, file)

    argparse.ArgumentParser._print_message = _print_message
    try:
        yield
    finally:
        argparse.ArgumentParser._print_message = original


# The words this process was invoked with, for a connected-mode wrapper to find
# the approval the gate consumed for this exact command.
INVOCATION: tuple[str, list[str]] | None = None


def main(argv: list[str] | None = None) -> int:
    global INVOCATION
    args = list(sys.argv[1:] if argv is None else argv)
    INVOCATION = ("torque", list(args))
    parsed = None
    try:
        if args and args[0] in PUBLIC_ROUTES:
            delegate, prefix = PUBLIC_ROUTES[args[0]]
            rest = args[1:]
            if args[0] == "recover" and rest[:1] == ["run"]:
                rest = ["exec", *rest[1:]]
            # The delegate's own subcommand name becomes the next argparse prog
            # token automatically; only rename the prog here when the public
            # route name differs from that underlying subcommand (e.g. recover/revert).
            display = "torque" if prefix[:1] == [args[0]] else f"torque {args[0]}"
            if args[0] == "recover":
                with _recover_grammar():
                    return _dispatch(delegate, [*prefix, *rest], display=display)
            return _dispatch(delegate, [*prefix, *rest], display=display)
        if args and args[0] in DELEGATES:
            return _dispatch(args[0], args[1:])
        args, tail = cli_approval.split_tail(args)
        parser = build_parser()
        parsed = parser.parse_args(args)
        if parsed.command is None:
            parser.print_help()
            return 0
        if parsed.command == "workspace":
            if parsed.action == "init":
                path = ws.init_workspace(parsed.path, parsed.name, parsed.profile)
                _print_json({"workspace": str(path), "org_calls": False}) if parsed.json else print(path)
            elif parsed.action == "ai-access":
                root = ws.set_ai_access(parsed.path, parsed.mode, parsed.approval, parsed.verify,
                                        parsed.approver_uid, delegated=parsed.delegated,
                                        model_id=parsed.model_id)
                shown = "connected (approval required)" if parsed.mode == "connected" else parsed.mode
                if parsed.json:
                    _print_json({"workspace": str(root), "ai_access": parsed.mode,
                                 **({"approval": parsed.approval} if parsed.approval else {})})
                else:
                    print(shown)
            elif parsed.action == "delegate":
                root = delegation.set_delegate(parsed.path, parsed.role, parsed.account, parsed.uid, parsed.kind)
                config = ws.load_workspace(root)[1]
                if parsed.json:
                    _print_json({"workspace": str(root), "delegates": config["delegates"]})
                else:
                    print(f"{parsed.role} delegate: {parsed.account} (uid {parsed.uid}, {parsed.kind})")
            else:
                from .template_updates import update_templates
                report = update_templates(Path(parsed.path), check=parsed.check)
                if parsed.json:
                    _print_json(report)
                else:
                    print(json.dumps(report, indent=2))
        elif parsed.command == "demo":
            from .demo import create_demo
            result = create_demo(Path(parsed.path))
            if parsed.json:
                _print_json(result)
            else:
                print(f"Synthetic demo ready: {result['workspace']}")
                print(f"Start here: {result['start_here']}")
                print("No Salesforce org, browser, model or paid account was used.")
        elif parsed.command in ("approval", "launch"):
            return cli_approval.run(parsed, tail)
        elif parsed.command == "client":
            if parsed.action == "consent":
                return cli_approval.run_consent(parsed)
            if parsed.action == "add":
                path = ws.add_client(parsed.workspace, parsed.name, parsed.org)
                _print_json({"client_root": str(path), "org_calls": False}) if parsed.json else print(path)
            else:
                records = ws.list_clients(parsed.workspace)
                if parsed.json:
                    _print_json(records)
                else:
                    for record in records:
                        print(f"{record['slug']}: {record['name']} (org: {record.get('org') or 'not configured'})")
                    if not records:
                        print("No clients yet. Use torque client add NAME --workspace PATH.")
        elif parsed.command == "change":
            return _change(parsed)
        elif parsed.command == "context":
            context = ws.get_context(parsed.workspace, parsed.client)
            if parsed.json:
                _print_json(context)
            else:
                print(f"{context['workspace']['name']} / {context['client']['name']}")
                print(f"Client directory: {context['client_root']}")
                print(f"Configured org: {context['client'].get('org') or 'not specified'}")
                print(context["evidence_note"])
                for title, value in context.get("notes", {}).items():
                    print(f"\n{title}:\n{value}")
                for change in context.get("changes", []):
                    print(f"\nChange {change['id']}: {change['title']} — {change['outcome']}")
                    unresolved = [f"{criterion['id']} ({criterion['reported_result'].replace('_', ' ')})"
                                  for criterion in change["criteria"] if criterion["reported_result"] != "pass"]
                    print("Acceptance results are operator-reported; independent business acceptance is not established.")
                    if unresolved:
                        print("Not yet reported pass: " + ", ".join(unresolved))
                    elif not change["criteria"]:
                        print("No acceptance criteria recorded.")
                    if change["assessment"]["evidence_problems"]:
                        print(f"Evidence problems: {change['assessment']['evidence_problems']} missing, changed or unavailable captures.")
                    for decision in change["decisions"]:
                        print(f"Decision (reported): {decision['summary']}")
                    for observation in change["metadata_observations"]:
                        print(f"Metadata {observation['result']} · Org {observation['target_org']} · Job {observation['job_id']}: {observation['summary']}")
                    for step in change["next_steps"]:
                        print(f"Next step (reported): {step['summary']}")
                    if any(change["history_truncated"].values()):
                        counts = ", ".join(f"{name}: {count}" for name, count in change["history_counts"].items())
                        print(f"{change['history_note']} Total entries — {counts}.")
                    print("Full change: " + change["show_command"])
                for entry in context["sessions"]:
                    print(f"\n{entry['created_at']} [{entry['status']}, user-reported] {entry['summary']}")
                    if entry["evidence_integrity"] in ("missing", "changed", "unavailable"):
                        print(f"Evidence: {entry['evidence_integrity']} since recording.")
        elif parsed.command == "session":
            if parsed.action == "add":
                entry = ws.add_session(parsed.workspace, parsed.client, parsed.summary, parsed.status, parsed.evidence)
                if parsed.json:
                    _print_json(entry)
                else:
                    print(f"Recorded {entry['id']} [{entry['status']}, user-reported]")
            else:
                entries = ws.list_sessions(parsed.workspace, parsed.client, limit=None)
                if parsed.action == "show":
                    entry = next((e for e in entries if e["id"] == parsed.entry_id), None)
                    if entry is None:
                        raise ws.WorkspaceError("session entry was not found for the selected client")
                    entries = [entry]
                if parsed.json:
                    _print_json(entries[0] if parsed.action == "show" else entries)
                else:
                    for entry in entries:
                        print(f"{entry['id']} [{entry['status']}, user-reported] {entry['summary']}")
                        if entry["evidence_integrity"] in ("missing", "changed", "unavailable"):
                            print(f"Evidence: {entry['evidence_integrity']} since recording.")
                    if not entries:
                        print("No session entries for this client.")
        elif parsed.command == "handoff":
            body = ws.render_handoff(parsed.workspace, parsed.client)
            if parsed.output:
                output = ws.client_output_path(parsed.workspace, parsed.client, parsed.output)
                ws.atomic_write_new(output, body)
                print(output)
            else:
                print(body, end="")
        elif parsed.command == "doctor":
            return _doctor(parsed)
        elif parsed.command == "workflows":
            return _workflows(parsed)
        return 0
    except delegation.Refusal as exc:
        # F37: a delegated setup verb (workspace ai-access, client consent record
        # or sign-off) refuses the same way cli_approval's delegated grant/deny
        # will: exit 3, with --json a machine-readable reason_class and message,
        # instead of falling into the generic WorkspaceError exit 2 below.
        if getattr(parsed, "json", False):
            _print_json({"refused": True, "reason_class": exc.reason_class, "message": str(exc)})
        else:
            print(f"torque: refused ({exc.reason_class}): {exc}", file=sys.stderr)
        return 3
    except (ws.WorkspaceError, OSError, ValueError) as exc:
        print(f"torque: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
