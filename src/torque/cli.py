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
    ai_access = work_sub.add_parser("ai-access", help="set the de-identified mode; the owner runs this, not an AI session")
    ai_access.add_argument("mode", choices=ws.AI_ACCESS_MODES)
    ai_access.add_argument("--path", default=".", help="workspace directory; defaults to the current directory")
    ai_access.add_argument("--json", action="store_true")
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
    for route in PUBLIC_ROUTES:
        sub.add_parser(route, add_help=False, help=f"{route} operations with selected-client evidence; use {route} --help")
    for route in DELEGATES:
        sub.add_parser(route, add_help=False, help=f"forward to the {route} workflow; use {route} --help")
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
            tracked = subprocess.run(["git", "ls-files", "--", *private_paths], cwd=root,
                                     capture_output=True, text=True, timeout=10)
            report["git_tracking"] = {"checked": tracked.returncode == 0,
                                      "tracked_private_paths": tracked.stdout.splitlines() if tracked.returncode == 0 else []}
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
        if report["client"]:
            print(f"Client: {report['client']['name']}")
        print(f"{selected}: {'local dependencies ready' if report['ready'] else 'missing local dependencies'}")
        for action in report["next_actions"]:
            print(f"Next: {action}")
        print("Local inspection only. No org authentication, API calls or runtime tests were performed.")
    return 0 if report["ready"] else 3


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
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
            return _dispatch(delegate, [*prefix, *rest], display=display)
        if args and args[0] in DELEGATES:
            return _dispatch(args[0], args[1:])
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
                root = ws.set_ai_access(parsed.path, parsed.mode)
                _print_json({"workspace": str(root), "ai_access": parsed.mode}) if parsed.json else print(parsed.mode)
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
        elif parsed.command == "client":
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
    except (ws.WorkspaceError, OSError, ValueError) as exc:
        print(f"torque: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
