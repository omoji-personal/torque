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
from pathlib import Path, PurePath, PureWindowsPath
import shutil
import sys
import subprocess

from . import __version__
from . import cli_approval
from . import delegation
from . import hosts
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
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--client", help="explicit client name or slug")
    scope.add_argument("--initiative", help="explicit internal initiative name or slug")


def _scope(parsed: argparse.Namespace) -> tuple[str, str]:
    """(name, kind) for the engagement a command names."""
    if getattr(parsed, "initiative", None):
        return parsed.initiative, "initiative"
    return parsed.client, "client"


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
    init.add_argument("--no-hook", action="store_true",
                      help="do not wire the gate hook in .claude/settings.json (docs/ai-access.md)")
    init.add_argument("--json", action="store_true")
    upgrade = work_sub.add_parser("upgrade", help="update packaged recipes while preserving local edits")
    upgrade.add_argument("path")
    upgrade.add_argument("--check", action="store_true", help="show available updates without writing")
    upgrade.add_argument("--diff", metavar="FILE", help="compare one managed file with its packaged text; writes nothing")
    upgrade.add_argument("--accept", metavar="FILE", action="append",
                         help="keep this local file, or every managed file in this folder, as your version: "
                              "records the packaged text it was compared with; writes only the manifest")
    upgrade.add_argument("--no-hook", action="store_true",
                         help="in a build-only workspace, do not add a missing gate hook")
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
    ai_access.add_argument("--no-hook", action="store_true",
                           help="full and build-only: add no gate hook (one Torque added for build-only mode "
                                "is still taken out when the workspace leaves it for full mode)")
    ai_access.add_argument("--json", action="store_true")
    guarded_reads = work_sub.add_parser("guarded-reads", help="switch guarded reads on or off for a connected "
                                                               "workspace; the owner runs this, not an AI session")
    guarded_reads.add_argument("value", choices=("on", "off"))
    guarded_reads.add_argument("--path", default=".", help="workspace directory; defaults to the current directory")
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
    initiative = sub.add_parser("initiative", help="manage an internal initiative's private context")
    initiative_sub = initiative.add_subparsers(dest="action", required=True)
    i_add = initiative_sub.add_parser("add")
    i_add.add_argument("name")
    i_add.add_argument("--workspace", required=True)
    i_add.add_argument("--owner", help="accountable person")
    i_add.add_argument("--json", action="store_true")
    for action in ("list", "show", "set-state"):
        p = initiative_sub.add_parser(action)
        p.add_argument("--workspace", required=True)
        p.add_argument("--json", action="store_true")
        if action != "list":
            p.add_argument("name")
        if action == "set-state":
            p.add_argument("state", choices=("active", "paused", "closed", "archived"))
            p.add_argument("--reason")
            p.add_argument("--review-date")
            p.add_argument("--outcome")
    engagement = sub.add_parser("engagement", help="list clients and initiatives together")
    engagement_sub = engagement.add_subparsers(dest="action", required=True)
    e_list = engagement_sub.add_parser("list")
    e_list.add_argument("--workspace", required=True)
    e_list.add_argument("--kind", choices=("client", "initiative"))
    e_list.add_argument("--json", action="store_true")
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
    from . import guarded
    guarded.register(sub)
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
            if (not isinstance(script, str) or not script or Path(script).is_absolute()
                    or PureWindowsPath(script).drive or PureWindowsPath(script).root or ":" in script
                    or any(part in ("", ".", "..") for part in re.split(r"[/\\]", script))):
                raise ws.WorkspaceError("config/parity.json script must be a relative file under this client's config directory")
            if not isinstance(source_org, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", source_org):
                raise ws.WorkspaceError("config/parity.json baseline_org must be a nonempty org alias")
            config_root = ws._inside(path, path / "config")
            adapter = ws._inside(config_root, config_root / script).resolve()
            if not adapter.is_relative_to(config_root.resolve()):
                raise ws.WorkspaceError("config/parity.json script must stay under this client's config directory")
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
    if not help_only:
        ws.require_package_writable(root_arg)
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


def _workflow_text(workspace: Path | None, slug: str) -> str | None:
    """The recipe `slug` as the selected workspace's host runs it. Claude Code's copy is
    .claude/commands/NAME.md and Antigravity's .agents/skills/NAME/SKILL.md, each under
    the name that host gives it (torque-NAME for one named like its own commands), then
    under the catalogue name a workspace not yet upgraded still has; a local copy is
    shown as it is, customizations included. Without one, the packaged recipe written
    for that host. Without a workspace, the packaged recipe with its catalogue names."""
    packaged = _data_text(f"{slug}.md")
    if workspace is None:
        return packaged
    host = hosts.for_workspace(ws.load_workspace(workspace)[1])
    for name in dict.fromkeys((hosts.recipe_name(host, slug), slug)):
        relative = (f".agents/skills/{name}/SKILL.md" if host is hosts.ANTIGRAVITY
                    else f".claude/commands/{name}.md")
        local = ws._inside(workspace, workspace.joinpath(*relative.split("/")))
        if local.exists():
            if not local.is_file():
                raise ws.WorkspaceError("selected local workflow must be a regular Markdown file")
            return local.read_text(encoding="utf-8")
    if packaged is None:
        return None
    if host is hosts.ANTIGRAVITY:
        from . import antigravity
        return antigravity.skill(slug, packaged) or hosts.rename_mentions(host, packaged)
    return ws.claude_copy("commands", f"{slug}.md", packaged)[1]


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
            recipe = _workflow_text(workspace, ws.slug_for(args.name))
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
    name, kind = _scope(args)
    common = (args.workspace, name)
    action = args.action
    # Reading skips a stray or damaged record and names it (ws.record_problem_lines).
    problems: list[dict] = []
    if action == "create":
        result = changes.create_change(*common, args.title, args.outcome, args.criterion, args.org,
                                       engagement_kind=kind)
    elif action == "list":
        result = changes.list_changes(*common, engagement_kind=kind, problems=problems)
    elif action in ("show", "handoff"):
        result = changes.get_change(*common, args.change_id, engagement_kind=kind, problems=problems)
        result["record_problems"] = problems
        if not args.json:
            text = changes.render_change(*common, args.change_id, engagement_kind=kind, problems=[])
            if problems:
                text += "\n" + "\n".join(["## Records not shown", "", *ws.record_problem_lines(problems)]) + "\n"
            if action == "handoff" and args.output:
                ws.atomic_write_new(ws.client_output_path(*common, args.output, kind=kind), text)
                print(args.output)
            else:
                print(text, end="")
            return 0
        if action == "handoff" and args.output:
            ws.atomic_write_new(ws.client_output_path(*common, args.output, kind=kind),
                                json.dumps(result, indent=2) + "\n")
    elif action == "note":
        result = changes.add_note(*common, args.change_id, args.text, args.kind, engagement_kind=kind)
    elif action == "check":
        result = changes.add_check(*common, args.change_id, args.criterion, args.result, args.summary,
                                   args.evidence, engagement_kind=kind)
    else:
        result = changes.verify_deploy(*common, args.change_id, args.org, args.job_id, args.component,
                                       args.manifest, engagement_kind=kind)
    if args.json:
        _print_json(result)
        if action == "list" and problems:
            print("\n".join(ws.record_problem_lines(problems)), file=sys.stderr)
    elif action == "list":
        for item in result:
            print(f"{item['id']}: {item['title']}")
        if not result:
            print("No changes yet. Use torque change create --help.")
        for line in ws.record_problem_lines(problems):
            print(line)
    else:
        print(f"Recorded {result['id']}: {result.get('summary', result.get('title', ''))}")
    return 3 if action == "verify-deploy" and result.get("result") != "pass" else 0


def _upgrade(args: argparse.Namespace) -> int:
    """`torque workspace upgrade`: a short summary by default, the whole report with --json."""
    from . import permissions
    from . import template_updates as updates
    if args.diff:
        print(updates.template_diff(Path(args.path), args.diff), end="")
        return 0
    if args.accept:
        if args.check:
            raise ws.WorkspaceError("--accept records your choice in the workspace manifest; run it without --check")
        report = updates.accept_templates(Path(args.path), args.accept)
        if args.json:
            _print_json(report)
            return 0
        accepted = [row["path"] for row in report["actions"] if row["action"] == "accepted"]
        for path in accepted:
            print(f"Accepted: {path} is kept as your version. It is a conflict again only when its packaged "
                  "text changes.")
        if len(report["actions"]) > len(accepted):
            print(f"{len(report['actions']) - len(accepted)} other file(s) named there needed nothing.")
        return 0
    report = updates.update_templates(Path(args.path), check=args.check)
    if ws.connected_rule_stale(args.path):
        report["connected_rule"] = (
            "`.claude/rules/production-approval.md` is from another version of Torque, and this "
            "command does not update it. The owner rewrites it with `torque workspace ai-access "
            "connected --approval required --path W` (with the --verify the workspace uses).")
    root, config = ws.load_workspace(args.path)
    mode = ws.access_mode(config)
    if mode == "build-only" and not args.no_hook:
        # Build-only mode is enforced by the hook, so a missing one is added, for both hosts.
        report["hooks"] = permissions.wire_gate_hooks(root, antigravity=True, check=args.check)
    elif mode == "full" and not args.no_hook:
        # In full mode the hook is the owner's choice (init and `ai-access full` add it): a missing one is
        # reported, and Torque's own shell-form entry is converted to exec form.
        report["hooks"] = permissions.wire_gate_hooks(root, check=args.check, add=False)
    if args.json:
        _print_json(report)
    else:
        print(_upgrade_summary(report, args.path, mode))
    return 3 if any(row["status"] == "failed" for row in report.get("hooks", [])) else 0


def _upgrade_summary(report: dict, path: str, mode: str) -> str:
    """One line of counts, then only the files that need something, each with its next command."""
    check = report["check"]
    actions = report["actions"]

    def command(*words: str) -> str:
        return ws.command_text(["torque", "workspace", "upgrade", path, *words])
    labels = {"current": "current", "adopt": "recorded as current", "customized": "customized and kept",
              "update": "to update" if check else "updated", "add": "to add" if check else "added",
              "renamed": "to rename" if check else "renamed", "accepted": "accepted",
              "preserve": "conflicts", "retired": "retired"}
    counts = report["counts"]
    lines = [f"{'Checked' if check else 'Upgraded'} {report['workspace']} against Torque {report['package_version']}"
             + ("; nothing was written." if check else "."),
             f"{len(actions)} files: " + ", ".join(f"{counts[key]} {label}" for key, label in labels.items()
                                                    if counts.get(key)) + "."]
    why = {"modified_local": "edited here, and the packaged text changed since it was recorded",
           "unmanaged_local": "a local file at a path Torque installs, which Torque never recorded",
           "changed_during_update": "it changed while the upgrade ran; rerun it"}
    for row in actions:
        name, action = row["path"], row["action"]
        if action == "update":
            lines.append(f"{'Update' if check else 'Updated'}: {name}"
                         + (" (now imports AGENTS.md)" if name == "CLAUDE.md" else
                            " (unchanged here; the packaged text changed)"))
        elif action == "add":
            lines.append(f"{'Add' if check else 'Added'}: {name}")
        elif action == "renamed":
            command_name = name.rsplit("/", 1)[-1][:-len(".md")]
            lines.append(f"{'Rename' if check else 'Renamed'}: {name} -> {row['renamed_to']} (Claude Code answers "
                         f"/{command_name} itself)" + ("; your edits move with it" if row["reason"] == "local_edit" else ""))
        elif action == "preserve" and row.get("reason") != "missing":
            if row.get("reason") == "renamed_both_present":
                lines.append(f"Conflict: {name}: {row['suggestion']} Compare: "
                             + command("--diff", row["renamed_to"]))
                continue
            line = f"Conflict: {name}: {why.get(row.get('reason'), row.get('suggestion', ''))}."
            if row.get("reason") in ("modified_local", "unmanaged_local"):
                line += f" Compare: {command('--diff', name)}; keep yours: {command('--accept', name)}"
            lines.append(line)
        elif action == "retired":
            lines.append(f"Retired: {name}: {row['suggestion']}")
    if counts.get("customized"):
        lines.append("Customized files are kept as they are and are not listed; --json lists every file.")
    for key in ("connected_rule", "claude_md"):
        if key in report:
            lines.append("Note: " + report[key])
    lines += _hook_lines(report.get("hooks", []), mode, path)
    return "\n".join(lines)


def _hook_lines(rows: list[dict], mode: str, path: str = ".") -> list[str]:
    """What wire_gate_hooks did, in plain lines; a hook already in place says nothing."""
    lines = []
    for row in rows:
        host, where = row["host"], row["path"]
        if row["status"] == "added":
            lines.append(f"Gate hook for {host}: added to {where}.")
        elif row["status"] == "converted":
            lines.append(f"Gate hook for {host}: Torque's entry in {where} now runs in exec form, with no shell.")
        elif row["status"] == "to_convert":
            lines.append(f"Gate hook for {host}: Torque's entry in {where} is in shell form; without --check it "
                         "is converted to exec form, which needs no shell.")
        elif row["status"] == "missing" and mode == "full":
            lines.append(f"Record protection: NOT IN FORCE; no gate hook for {host} in {where}. Wire it with "
                         + ws.command_text(["torque", "workspace", "ai-access", "full", "--path", str(path)]) + ".")
        elif row["status"] == "missing":
            lines.append(f"Gate hook for {host}: missing from {where}; the upgrade without --check adds it.")
        elif row["status"] == "failed":
            lines += [f"Gate hook for {host}: NOT ADDED ({row['reason']}). Add this entry to {where} yourself:",
                      json.dumps(row["entry"], ensure_ascii=False)]
    if mode == "full" and any(row["status"] == "added" for row in rows):
        lines.append("In full mode the hook only keeps tools from changing a client's consent, approval and "
                     "control records. --no-hook leaves it out.")
    return lines


def _hook_verification(root: Path, rows: list[dict]) -> list[str]:
    """Whether the hooks just wired pass doctor's standalone probes."""
    if not any(row["status"] in ("added", "present", "converted") for row in rows):
        return []
    access = _gate_hook_report(Path(root))
    doctor = ws.command_text(["torque", "doctor", "--workspace", str(root)])
    lines = []
    for host, report in (("Claude Code", access["hook"]), ("Antigravity", access["antigravity_hook"])):
        if any(row["host"] == host for row in rows) and report["configured"]:
            lines.append(f"{host} hook: " + ("blocked a standalone probe; host enforcement not tested."
                                             if report["verified"] else f"did not pass its standalone probe; run {doctor}."))
    return lines


# The gate blocks tools it does not recognise, so the hook must see every tool
# call. A matcher is a regular expression over the tool name ("" and "*" match
# all); it covers the tools when it matches each of these, including a made-up
# name standing for tools a host adds later.
_HOOK_COVERAGE_PROBES = ("Bash", "Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "NotebookRead", "LS",
                         "Grep", "Glob", "Monitor", "PowerShell", "WebFetch", "Task", "mcp__server__tool",
                         "FutureTool")
# The same for Antigravity's hook, in its tool names (1.3.1's, and 1.4.9's file and connector tools).
_AGY_COVERAGE_PROBES = ("run_command", "manage_task", "send_command_input", "view_file", "list_dir", "grep_search",
                        "find_by_name", "write_to_file", "replace_file_content", "multi_replace_file_content",
                        "read_file", "write_file", "edit_file", "call_mcp_tool", "mcp_server_tool",
                        "open_browser_url", "invoke_subagent", "future_tool")


def _matcher_covers(matchers: list[str], probes: tuple[str, ...] = _HOOK_COVERAGE_PROBES) -> bool:
    """True when the union of the hook entries' matchers matches every probe name."""
    def matches(matcher: str, name: str) -> bool:
        if matcher.strip() in ("", "*"):
            return True
        try:
            return re.fullmatch(matcher, name) is not None
        except re.error:
            return False
    return bool(matchers) and all(any(matches(m, name) for m in matchers) for name in probes)


def _managed_settings_dir(platform: str | None = None, environ=None) -> Path:
    """Where Claude Code reads its file-based managed settings: managed-settings.json
    and a managed-settings.d folder beside it (code.claude.com/docs/en/managed-settings)."""
    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    if platform == "darwin":
        return Path("/Library/Application Support/ClaudeCode")
    if platform.startswith("win"):
        return Path(environ.get("ProgramW6432") or environ.get("ProgramFiles") or "C:\\Program Files") / "ClaudeCode"
    return Path("/etc/claude-code")


def _legacy_managed_settings(environ=None) -> Path | None:
    """The Windows managed-settings file of older Claude Code versions, which current
    versions do not read; None elsewhere."""
    environ = os.environ if environ is None else environ
    if os.name != "nt":
        return None
    return Path(environ.get("ProgramData") or "C:\\ProgramData") / "ClaudeCode" / "managed-settings.json"


def _registry_policy() -> dict | None:
    """Claude Code's policy in the Windows registry (the Settings value under
    HKLM\\SOFTWARE\\Policies\\ClaudeCode), an admin source above the managed files;
    None when there is none or it cannot be read."""
    if os.name != "nt":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\ClaudeCode") as key:
            value, _kind = winreg.QueryValueEx(key, "Settings")
        data = json.loads(value)
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


# The two settings that keep Claude Code from running the workspace's hook.
_HOOK_SWITCHES = ("disableAllHooks", "allowManagedHooksOnly")
# Keys that do not make a managed source the one Claude Code applies.
_CONTROL_KEYS = ("wslInheritsWindowsSettings", "managedSourcesBehavior")


def _settings_file(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _managed_policy() -> tuple[dict, dict, list[str]]:
    """The managed values of the hook switches doctor can see, with the source of
    each, and the managed files it read. Claude Code applies the highest admin source
    that sets a policy key: server-managed settings (not visible to doctor), then the
    registry or a macOS configuration profile (the registry is read here, a profile is
    not), then managed-settings.json merged with managed-settings.d/*.json in name
    order, a later file's value replacing an earlier one. Only true or false counts."""
    registry = _registry_policy()
    if registry and any(key not in _CONTROL_KEYS for key in registry):
        label = r"HKLM\SOFTWARE\Policies\ClaudeCode (Settings)"
        values = {key: registry[key] for key in _HOOK_SWITCHES if isinstance(registry.get(key), bool)}
        return values, {key: label for key in values}, []
    folder = _managed_settings_dir()
    files = [folder / "managed-settings.json"]
    drop_ins = folder / "managed-settings.d"
    if drop_ins.is_dir():
        files += sorted((path for path in drop_ins.iterdir()
                         if path.suffix == ".json" and not path.name.startswith(".") and path.is_file()),
                        key=lambda path: path.name)
    values, sources, read = {}, {}, []
    for path in files:
        data = _settings_file(path)
        if data is None:
            continue
        read.append(str(path))
        for key in _HOOK_SWITCHES:
            if isinstance(data.get(key), bool):
                values[key], sources[key] = data[key], str(path)
    return values, sources, read


def _user_settings() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(configured) if configured else Path.home() / ".claude") / "settings.json"


def _hooks_disabled_by(root: Path) -> list[str]:
    """The settings that keep Claude Code from running this workspace's hook, by the
    documented precedence: a managed allowManagedHooksOnly, and the disableAllHooks
    of the highest level that sets it (managed, then the workspace's local and
    shared settings, then the user's)."""
    values, sources, _ = _managed_policy()
    found = []
    if values.get("allowManagedHooksOnly"):
        found.append(f"{sources['allowManagedHooksOnly']} (allowManagedHooksOnly)")
    if "disableAllHooks" in values:
        if values["disableAllHooks"]:
            found.append(f"{sources['disableAllHooks']} (disableAllHooks)")
        return found
    for path in (root / ".claude" / "settings.local.json", root / ".claude" / "settings.json", _user_settings()):
        value = (_settings_file(path) or {}).get("disableAllHooks")
        if isinstance(value, bool):
            if value:
                found.append(f"{path} (disableAllHooks)")
            break
    return found


def _managed_settings_report(root: Path) -> dict:
    """What doctor read of the managed policy, and what it cannot see."""
    _, _, read = _managed_policy()
    legacy = _legacy_managed_settings()
    return {"files_read": read, "ignored": [str(legacy)] if legacy is not None and legacy.is_file() else [],
            "not_checked": "server-managed settings and macOS configuration profiles"}
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


def _run_hook_probe(command, root: Path, event: str) -> tuple[int | None, str]:
    """Run one hook the way Claude Code runs it: an exec-form argument vector (a tuple)
    directly, with no shell; a shell-form command line through Git Bash on Windows and
    the default shell elsewhere. (exit code or None, stderr)."""
    try:
        if isinstance(command, tuple):
            run = subprocess.run(list(command), cwd=root, input=event, capture_output=True, text=True, timeout=60)
            return run.returncode, run.stderr
        shell = _hook_shell()
        if shell:
            run = subprocess.run([shell, "-c", command], cwd=root, input=event, capture_output=True,
                                 text=True, timeout=60)
        else:
            run = subprocess.run(command, shell=True, cwd=root, input=event, capture_output=True,
                                 text=True, timeout=60)
        return run.returncode, run.stderr
    except subprocess.TimeoutExpired:
        return None, "the hook did not finish within 60 seconds"
    except OSError as exc:
        return None, f"the hook could not start: {exc}"


def _isolated_call(call) -> bool:
    """The hook's Python runs isolated: -I, alone or in an option group, before -c or -m."""
    if isinstance(call, str):
        return bool(_HOOK_ISOLATED_RE.match(call))
    for word in call[1:]:
        if word in ("-c", "-m") or not word.startswith("-"):
            return False
        if re.fullmatch(r"-[A-Za-z]*I[A-Za-z]*", word):
            return True
    return False


# The first Claude Code version that honours "onFailure" on a hook entry.
ON_FAILURE_VERSION = (2, 1, 295)


_CLAUDE_VERSIONS: dict[str, tuple[int, ...] | None] = {}


def _claude_code_version(environ=None) -> tuple[int, ...] | None:
    """The installed Claude Code's version, from `claude --version`, or None when it is
    not on PATH or does not say. Only PATH's absolute folders are searched: Windows
    would also look in the current folder, which can be the workspace. Asked once per
    process for each program found."""
    from jsc_common import tools
    names = ("claude.exe", "claude.cmd", "claude.bat") if os.name == "nt" else ("claude",)
    folders = [folder for folder in ((os.environ if environ is None else environ).get("PATH") or "").split(os.pathsep)
               if folder and os.path.isabs(folder)]
    path = next((candidate for folder in folders for candidate in (os.path.join(folder, name) for name in names)
                 if os.path.isfile(candidate) and os.access(candidate, os.X_OK)), None)
    if path is None:
        return None
    if path not in _CLAUDE_VERSIONS:
        try:
            run = tools.run([path, "--version"], capture_output=True, text=True, timeout=10)
            match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", run.stdout or "") if run.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            match = None
        _CLAUDE_VERSIONS[path] = tuple(int(part) for part in match.groups()) if match else None
    return _CLAUDE_VERSIONS[path]


_ON_FAILURE_ADVICE = ('Set "onFailure": "block" on the torque.gate hook entry (Claude Code 2.1.295 and later). '
                      "Without it, a hook that cannot start, for example because its interpreter moved, lets "
                      "every call run ungated.")


def _hook_problem(hook: dict, consequence: str, probe: str = "client-path") -> str:
    """Why a wired Claude Code hook is not in force; the caller appends the entry that fixes it."""
    error = hook["probe_error"]
    if hook["blocks_every_call"]:
        return ("The torque.gate hook could not run the gate (" + (error or "it did not start") + "), so Claude "
                "Code blocks every tool call, ordinary work included. Point the hook at an interpreter with Torque "
                "installed, using this entry: ")
    if hook["allow_probe_exit"] not in (0, 2, None):
        return ("The torque.gate hook could not start (" + (error or f"exit {hook['allow_probe_exit']}") + "), and "
                'without "onFailure": "block" Claude Code then runs each call ungated, ' + consequence + ". Point "
                "the hook at an interpreter with Torque installed, using this entry: ")
    return (f"The torque.gate hook did not block a {probe} probe (exit {hook['probe_exit']}), {consequence}. Point "
            "the hook at an interpreter with Torque installed, using this entry: ")


def _synced_folder(path, environ=None) -> str | None:
    """The file-sync service whose folder holds `path`, judged by folder names and
    Windows' OneDrive variables; None when none is recognised. A sync service can
    evict a file to save space, and an evicted interpreter cannot start a hook."""
    environ = os.environ if environ is None else environ
    folded = os.path.normcase(str(path))
    for variable in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        base = environ.get(variable)
        if base and folded.startswith(os.path.normcase(base.rstrip("\\/")) + os.sep):
            return "OneDrive"
    for part in (path if isinstance(path, PurePath) else PurePath(path)).parts:
        name = part.casefold()
        if name.startswith("onedrive"):
            return "OneDrive"
        if name.startswith("dropbox"):
            return "Dropbox"
        if name in ("google drive", "my drive") or name.startswith("googledrive"):
            return "Google Drive"
        if name in ("icloud drive", "mobile documents", "com~apple~clouddocs") or name.startswith("icloud"):
            return "iCloud Drive"
    return None


def _gate_hook_report(root: Path) -> dict:
    """Inspect the workspace's own Claude Code hook and, in full and build-only mode,
    run it on two synthetic calls: one it must block (a client-path Read in build-only
    mode, a write to a client's consent record in full mode) and an ordinary Read it
    must allow, so a hook that cannot start or blocks everything is told apart from
    one in force. Makes no org call and reads no client file."""
    from . import gate, permissions
    mode_root, mode, known = gate._workspace_mode(root)
    python = sys.executable.replace("\\", "/")
    hook: dict = {"configured": False, "commands": [], "matcher_covers_tools": False, "exec_form": False,
                  "convertible": False,
                  "fail_closed_shim": False, "fails_closed_on_start": False, "isolated": False, "disabled_by": [],
                  "verified": None, "probe_exit": None, "allow_probe_exit": None, "blocks_every_call": False,
                  "timeouts": [], "probe_error": "", "recommended_command": gate.hook_command(python),
                  "recommended_entry": {"hooks": {"PreToolUse": [{"matcher": ".*", "hooks": [
                      permissions.gate_hook_item(python)]}]}}}
    matchers: list[str] = []
    on_failure: list = []
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
                # Exec form (an argument vector) or shell form (a command line); both run the gate.
                call = permissions.gate_hook_call(item)
                if call is not None:
                    hook["commands"].append(call)
                    hook["timeouts"].append(item.get("timeout"))
                    on_failure.append(item.get("onFailure"))
                    hook["configured"] = True
                    matchers.append(matcher)
                    if isinstance(call, str) and permissions.torque_hook_python(item) is not None:
                        hook["convertible"] = True
    hook["matcher_covers_tools"] = _matcher_covers(matchers)
    hook["disabled_by"] = _hooks_disabled_by(root)
    hook["managed_settings"] = _managed_settings_report(root)
    hook["fail_closed_shim"] = bool(hook["commands"]) and all(
        "sys.excepthook" in (c if isinstance(c, str) else " ".join(c)) for c in hook["commands"])
    # Claude Code 2.1.295 and later: a hook that cannot start blocks the call instead of letting it run.
    hook["fails_closed_on_start"] = bool(on_failure) and all(value == "block" for value in on_failure)
    hook["isolated"] = bool(hook["commands"]) and all(_isolated_call(c) for c in hook["commands"])
    hook["exec_form"] = bool(hook["commands"]) and all(isinstance(c, tuple) for c in hook["commands"])
    if mode == "build-only":
        hook["verified"] = False
    if mode in ("build-only", "full") and hook["commands"]:
        target = {"file_path": "clients/.torque-doctor-probe/probe.md"} if mode == "build-only" else {
            "file_path": "clients/.torque-doctor-probe/consent.json", "content": "{}"}
        blocked = json.dumps({"tool_name": "Read" if mode == "build-only" else "Write", "cwd": str(root),
                              "tool_input": target})
        ordinary = json.dumps({"tool_name": "Read", "cwd": str(root), "tool_input": {"file_path": "AGENTS.md"}})
        results = [_run_hook_probe(command, root, blocked) for command in hook["commands"]]
        allowed = [_run_hook_probe(command, root, ordinary) for command in hook["commands"]]
        hook["probe_exit"], hook["allow_probe_exit"] = results[0][0], allowed[0][0]
        hook["verified"] = (all(code == 2 and "could not" not in err for code, err in results)
                            and all(code == 0 for code, _ in allowed))
        # Exit 2 blocks the call; a hook that cannot start blocks it only with onFailure "block".
        hook["blocks_every_call"] = any(code == 2 or (code != 0 and hook["fails_closed_on_start"])
                                        for code, _ in allowed)
        failed = ([err for code, err in results if code == 2 and "could not" in err]
                  + [err for code, err in allowed if code != 0])
        if failed:
            hook["probe_error"] = (failed[0].strip().splitlines() or [""])[-1][:300]
    return {"mode": mode, "mode_known": known, "governing_workspace": str(mode_root), "hook": hook,
            "antigravity_hook": _antigravity_hook_report(root, mode)}


def _antigravity_hook_report(root: Path, mode: str) -> dict:
    """The same for Antigravity, which reads `.agents/hooks.json` and never
    `.claude/`: whether the gate is registered there for every tool and, in
    build-only mode, whether it blocks a synthetic client-path read when run
    the way Antigravity runs a hook (through the shell, from `.agents/`)."""
    from . import gate_antigravity as agy
    hook: dict = {"configured": False, "commands": [], "matcher_covers_tools": False, "isolated": False,
                  "disabled": False, "verified": None, "probe_decision": None, "probe_error": "",
                  "blocks_every_call": False, "recommended_command": agy.hook_command(sys.executable),
                  "recommended_entry": agy.hook_entry(sys.executable)}
    matchers: list[str] = []
    try:
        data = json.loads((root / hosts.ANTIGRAVITY.hook_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    for spec in (data.values() if isinstance(data, dict) else []):
        entries = spec.get("PreToolUse") if isinstance(spec, dict) else None
        for entry in entries if isinstance(entries, list) else []:
            for item in (entry.get("hooks") or []) if isinstance(entry, dict) else []:
                command = str(item.get("command") or "") if isinstance(item, dict) else ""
                if agy.HOOK_MODULE in command:
                    hook["configured"] = True
                    # Entries are judged one by one: a disabled one is not run, here or by Antigravity,
                    # and hides no enabled one. Commands, coverage and isolation are the enabled entries'.
                    if spec.get("enabled") is not False:
                        hook["commands"].append(command)
                        matchers.append(str(entry.get("matcher") or ""))
    hook["matcher_covers_tools"] = _matcher_covers(matchers, _AGY_COVERAGE_PROBES)
    hook["isolated"] = bool(hook["commands"]) and all(_HOOK_ISOLATED_RE.match(c) for c in hook["commands"])
    # Disabled: registered, and no enabled entry remains.
    hook["disabled"] = hook["configured"] and not hook["commands"]
    active = hook["commands"]
    if mode in ("build-only", "full") and active:
        # The same two probes as Claude Code's: a client-path read in build-only mode, a write to a
        # client's consent record in full mode, each under the tool names of 1.3.1 and of 1.4.9, and an
        # ordinary read inside the session that must not be refused (a hook that blocks it blocks all work).
        folder = root / "clients" / ".torque-doctor-probe"
        if mode == "build-only":
            blocked = [{"name": "view_file", "args": {"AbsolutePath": str(folder / "probe.md")}},
                       {"name": "read_file", "args": {"Path": str(folder / "probe.md")}}]
        else:
            blocked = [{"name": "write_to_file", "args": {"TargetFile": str(folder / "consent.json"),
                                                          "CodeContent": "{}"}},
                       {"name": "write_file", "args": {"Path": str(folder / "consent.json"), "Content": "{}"}}]
        ordinary = [{"name": "view_file", "args": {"AbsolutePath": str(root / "AGENTS.md")}},
                    {"name": "read_file", "args": {"Path": str(root / "AGENTS.md")}}]

        def ask(call):
            probe = json.dumps({"toolCall": call, "workspacePaths": [str(root)], "conversationId": "torque-doctor"})
            rows = []
            for command in active:
                try:
                    run = subprocess.run(command, shell=True, cwd=root / ".agents", input=probe, capture_output=True,
                                         text=True, timeout=60)
                    answer = json.loads(run.stdout)
                    answer = answer if isinstance(answer, dict) else {}
                    # Antigravity blocks a call whose hook exits non-zero, whatever it printed.
                    rows.append((answer.get("decision") if run.returncode == 0 else None,
                                 str(answer.get("reason") or "")))
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    rows.append((None, ""))
            return rows
        stopped = [row for call in blocked for row in ask(call)]
        let_through = [row for call in ordinary for row in ask(call)]
        hook["probe_decision"] = stopped[0][0]
        hook["verified"] = (all(d == "deny" and "could not" not in r for d, r in stopped)
                            and all(d in ("allow", "ask") for d, _ in let_through))
        hook["blocks_every_call"] = any(d != "allow" and d != "ask" for d, _ in let_through)
        failed = [r for d, r in stopped + let_through if d == "deny" and "could not" in r]
        if failed:
            hook["probe_error"] = failed[0][:300]
    return hook


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
    in_use = hosts.DEFAULT
    if args.workspace:
        root, firm = ws.load_workspace(args.workspace)
        report["workspace"] = {"path": str(root), "name": firm["name"], "profile": firm["profile"]}
        # The host workspace.json names is the one whose hook decides readiness.
        in_use = hosts.for_workspace(firm)
        report["ai_access"] = {**_gate_hook_report(Path(root)), "host": in_use.key}
        if args.client:
            client, _, data = ws.load_client(root, args.client)
            # Reading the selected journal also checks its local format, without evaluating claims.
            skipped: list[dict] = []
            entries = ws.list_sessions(root, args.client, limit=None, problems=skipped)
            from .changes import get_change, list_changes
            changes = [get_change(root, args.client, change["id"], problems=skipped)
                       for change in list_changes(root, args.client, problems=skipped)]
            evidence_problems = sum(entry["evidence_integrity"] in ("missing", "changed", "unavailable")
                                    for entry in entries)
            evidence_problems += sum(change["assessment"]["evidence_problems"] for change in changes)
            report["client"] = {"path": str(client), "name": data["name"],
                                "recent_sessions": min(len(entries), 20), "sessions_checked": len(entries),
                                "changes_checked": len(changes), "evidence_problems": evidence_problems,
                                "record_problems": len(skipped), "skipped_records": skipped}
        report["synced_folders"] = {"workspace": _synced_folder(root), "interpreter": _synced_folder(sys.executable)}
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
    claude_in_use = in_use is hosts.CLAUDE

    def claude_finding(text: str, blocks: bool = True) -> None:
        """A finding about Claude Code's hook. It decides readiness only when Claude
        Code is the workspace's host; under another host it is advice."""
        if claude_in_use:
            if blocks:
                report["ready"] = False
            report["next_actions"].append(text)
        else:
            report["next_actions"].append("Claude Code (not this workspace's host): " + text)

    wire = ws.command_text(["torque", "workspace", "ai-access", access["mode"] if access else "full",
                            "--path", str(args.workspace or ".")])
    claude_entry = json.dumps(access["hook"]["recommended_entry"], ensure_ascii=False) if access else ""
    if access and access["mode"] == "build-only":
        hook = access["hook"]
        if not hook["verified"]:
            if not hook["configured"]:
                if claude_in_use:
                    claude_finding(
                        "Build-only mode is set but no torque.gate hook is wired in this workspace's "
                        ".claude/settings.json, so nothing is blocked. Wire it with " + wire + ", or add this "
                        "entry to .claude/settings.json (docs/ai-access.md): " + claude_entry)
                else:
                    report["next_actions"].append(
                        "Claude Code reads .claude/settings.json, never .agents/hooks.json, and no torque.gate "
                        "hook is wired there, so a session started with Claude Code in this workspace is not "
                        "gated. If Claude Code is used here, wire it with " + wire + ", or add this entry to "
                        ".claude/settings.json: " + claude_entry)
            else:
                claude_finding(_hook_problem(hook, "so build-only mode is not in force") + claude_entry)
        else:
            if not hook["fail_closed_shim"]:
                claude_finding(
                    "The torque.gate hook works, but fails open if its interpreter later loses Torque. "
                    "Switch to the fail-closed hook entry: " + claude_entry, blocks=False)
            if not hook["isolated"]:
                claude_finding(
                    "The torque.gate hook runs Python without -I (isolated mode), so a torque/ folder or "
                    "sitecustomize.py written into the workspace can replace the gate. Switch to: " + claude_entry)
            if not hook["fails_closed_on_start"]:
                claude_finding(_ON_FAILURE_ADVICE, blocks=False)
        slow = [t for t in hook["timeouts"] if not isinstance(t, (int, float)) or isinstance(t, bool)
                or t > HOOK_TIMEOUT]
        if hook["configured"] and slow:
            from . import gate
            claude_finding(
                f'Set "timeout": {HOOK_TIMEOUT} on the torque.gate hook entry (it has '
                + ("none" if slow[0] is None else repr(slow[0])) + "). A hook that times out lets the call "
                f"proceed; the gate blocks by itself once its {gate.GATE_TIME_BUDGET:g}-second budget runs out, "
                "well inside that timeout.", blocks=False)
        agy = access["antigravity_hook"]
        entry = ("the torque-gate entry docs/ai-access.md shows, with this command: " + agy["recommended_command"])
        if not agy["configured"]:
            if in_use is hosts.ANTIGRAVITY:
                report["ready"] = False
                report["next_actions"].append(
                    "workspace.json names Antigravity as this workspace's host, but no torque.gate_antigravity "
                    "hook is registered in .agents/hooks.json, so a session started with Antigravity here is "
                    "not gated and build-only mode is not in force. Add to .agents/hooks.json " + entry)
            elif (Path(root) / ".agents").is_dir():
                report["next_actions"].append(
                    "Antigravity reads .agents/hooks.json, never .claude/settings.json, and no "
                    "torque.gate_antigravity hook is registered there, so a session started with Antigravity "
                    "in this workspace is not gated. If Antigravity is used here, add to .agents/hooks.json "
                    + entry)
        elif agy["disabled"]:
            # A disabled entry is not run, here or by Antigravity.
            report["ready"] = False
            report["next_actions"].append(
                "The Antigravity hook is disabled, so Antigravity does not run it and some calls are not gated. "
                "Use " + entry)
        elif not agy["verified"]:
            report["ready"] = False
            if agy["probe_error"] or agy["probe_decision"] is None or agy["blocks_every_call"]:
                report["next_actions"].append(
                    "The Antigravity hook could not run the gate ("
                    + (agy["probe_error"] or "it printed no decision") + "), so Antigravity blocks every tool "
                    "call, ordinary work included. Point it at an interpreter with this Torque version "
                    "installed. Use " + entry)
            else:
                report["next_actions"].append(
                    f"The Antigravity hook did not block a client-path probe (decision {agy['probe_decision']}), "
                    "so build-only mode is not in force in Antigravity. Point it at an interpreter with Torque "
                    "installed. Use " + entry)
        else:
            if not agy["matcher_covers_tools"]:
                report["ready"] = False
                report["next_actions"].append(
                    "The Antigravity hook works but does not see every tool call (its matcher "
                    "is narrower than \"*\"), so some calls are not gated. Use " + entry)
            if not agy["isolated"]:
                report["ready"] = False
                report["next_actions"].append(
                    "The Antigravity hook runs Python without -I (isolated mode), so a torque/ folder or "
                    "sitecustomize.py written into the workspace can replace the gate. Use " + entry)
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
        if hook["disabled_by"] and (claude_in_use or hook["configured"]):
            claude_finding(
                "Claude Code will not run this workspace's hook: " + "; ".join(hook["disabled_by"])
                + ". Remove disableAllHooks (or allowManagedHooksOnly) so the torque.gate hook runs.")
        if hook["configured"] and not hook["matcher_covers_tools"]:
            claude_finding(
                'The hook matcher does not cover every tool call. Set it to ".*": the gate checks '
                "command-running tools such as Monitor and blocks tools it does not recognise, but only "
                "for the calls the matcher sends it.")
    if access and access["mode"] == "full":
        # In full mode the hook only keeps tools from changing a client's consent, approval
        # and control records; a missing one is advice, one that blocks every call is not.
        hook = access["hook"]
        if not hook["configured"]:
            report["next_actions"].append(
                "Record protection: NOT IN FORCE. No torque.gate hook is wired in .claude/settings.json, so "
                "nothing stops a Claude Code tool from changing a client's consent, approval or control "
                "records. Wire it with " + wire + ".")
        elif not hook["verified"]:
            claude_finding(_hook_problem(hook, "so record protection is not in force", "record")
                           + claude_entry, blocks=hook["blocks_every_call"])
        elif not hook["fails_closed_on_start"]:
            claude_finding(_ON_FAILURE_ADVICE, blocks=False)
        if hook["configured"] and hook["disabled_by"]:
            claude_finding("Claude Code will not run this workspace's hook: " + "; ".join(hook["disabled_by"])
                           + ", so record protection is not in force.", blocks=False)
        agy = access["antigravity_hook"]
        if not agy["configured"]:
            if not claude_in_use:
                report["next_actions"].append(
                    "Record protection under Antigravity: NOT IN FORCE. No torque.gate_antigravity hook is "
                    "registered in .agents/hooks.json. Wire it with " + wire + ".")
        elif agy["disabled"]:
            # Antigravity does not run a disabled entry, and neither does doctor: the owner's choice, advice only.
            report["next_actions"].append(
                "Record protection under Antigravity: NOT IN FORCE. The torque.gate_antigravity hook in "
                ".agents/hooks.json is disabled. Enable it, or leave it off if that is intended.")
        elif not agy["verified"]:
            # An active hook that cannot run the gate, or refuses ordinary work, blocks every Antigravity tool call.
            report["ready"] = False
            startup = agy["probe_error"] or agy["probe_decision"] is None or agy["blocks_every_call"]
            report["next_actions"].append(
                "Record protection under Antigravity: NOT IN FORCE. The torque.gate_antigravity hook in "
                ".agents/hooks.json did not block a probe write to a client's consent record"
                + (" (the hook could not run the gate, exited non-zero or refused an ordinary read"
                   + (": " + agy["probe_error"] if agy["probe_error"] else "") + "), so Antigravity blocks every "
                   "tool call, ordinary work included" if startup else "")
                + ". Point it at an interpreter with this Torque version installed. Use the torque-gate entry "
                "docs/ai-access.md shows, with this command: " + agy["recommended_command"])
        elif not agy["matcher_covers_tools"] or not agy["isolated"]:
            # Chosen on purpose or still working: advice only in full mode.
            report["next_actions"].append(
                "Record protection under Antigravity: NOT IN FORCE for some calls. The torque.gate_antigravity "
                "hook in .agents/hooks.json covers fewer tools than \"*\" or runs Python without -I. Use "
                "the torque-gate entry docs/ai-access.md shows, with this command: " + agy["recommended_command"])
    if access and access["mode"] in ("full", "build-only") and access["hook"]["configured"] \
            and not access["hook"]["exec_form"] and os.name == "nt":
        fix = ("convert Torque's own entry with "
               + ws.command_text(["torque", "workspace", "upgrade", str(args.workspace)])
               if access["hook"]["convertible"] else "use this exec-form entry instead: " + claude_entry)
        claude_finding("The torque.gate hook is in shell form, which Claude Code runs through Git Bash on Windows, "
                       "and through PowerShell, which cannot run it, when Git Bash is not installed. Exec form "
                       "(command and args) needs no shell: " + fix + ".", blocks=False)
    if access and access["hook"]["configured"] and access["hook"]["fails_closed_on_start"]:
        # The entries rely on "onFailure", which older versions ignore.
        version = _claude_code_version()
        report["claude_code"] = {"version": ".".join(map(str, version)) if version else None}
        if version and version < ON_FAILURE_VERSION:
            claude_finding(f"Claude Code {'.'.join(map(str, version))} is older than 2.1.295 and ignores "
                           '"onFailure": "block", so a gate hook that cannot start lets every call run. Update '
                           "Claude Code.", blocks=False)
    if access:
        for path in access["hook"]["managed_settings"]["ignored"]:
            report["next_actions"].append(
                f"{path} is the managed-settings location of older Claude Code versions; current versions do not "
                f"read it, so nothing in it applies. An administrator moves it to {_managed_settings_dir()}.")
    synced = report.get("synced_folders") or {}
    if synced.get("workspace"):
        report["next_actions"].append(
            f"The workspace is in a {synced['workspace']} folder. A sync service can evict files to save space, "
            "and an evicted record or recipe then reads as missing. Keep the folder downloaded on this device, or "
            "move the workspace to a folder that is not synced.")
    if synced.get("interpreter") and access and (access["hook"]["configured"] or access["antigravity_hook"]["configured"]):
        report["next_actions"].append(
            f"Torque's interpreter ({sys.executable}) is in a {synced['interpreter']} folder. When the sync service "
            "evicts it, the gate hook cannot start, and Claude Code then blocks every call (with onFailure "
            "\"block\") or runs every call ungated. Install Torque in a folder that is not synced.")
    if report["client"] and report["client"]["record_problems"]:
        report["ready"] = False
        skipped = report["client"]["skipped_records"]
        shown = ", ".join(problem["path"] for problem in skipped[:5]) + (" ..." if len(skipped) > 5 else "")
        report["next_actions"].append(
            f"{len(skipped)} record(s) in this client's folder could not be read, so context and handoff skip them "
            f"and say so: {shown}. Torque never rewrites a record: check each file, then move it out of the "
            "folder or delete it.")
    if access and access["mode"] == "connected":
        from . import doctor_connected
        connected = doctor_connected.report(Path(root), args.client, live=getattr(args, "live", False))
        access["connected"] = connected
        if not connected["ready"]:
            report["ready"] = False
        report["next_actions"] += [f"Connected mode: {problem.rstrip('.')}." for problem in connected["problems"]]
        report["next_actions"] += [f"Connected mode (advice): {note.rstrip('.')}." for note in connected["advice"]]
        if not claude_in_use:
            # doctor_connected reads Claude Code's hook, its permission rules and its
            # answer format; nothing above says an Antigravity session is gated.
            report["ready"] = False
            agy = access["antigravity_hook"]
            if not agy["configured"]:
                report["next_actions"].append(
                    "workspace.json names Antigravity as this workspace's host, but no torque.gate_antigravity "
                    "hook is registered in .agents/hooks.json, so a session started with Antigravity here is "
                    "not gated. Add to .agents/hooks.json the torque-gate entry docs/ai-access.md shows, with "
                    "this command: " + agy["recommended_command"])
            report["next_actions"].append(
                f"workspace.json names {in_use.name} as this workspace's host. Doctor checks connected mode for "
                "Claude Code only (its hook, its permission rules and the probes above), so it cannot say this "
                "workspace is ready and reports it as not ready. See docs/hosts.md.")
    if report["client"] and report["client"]["evidence_problems"]:
        report["next_actions"].append(
            f"Review {report['client']['evidence_problems']} missing, changed or unavailable evidence references "
            "in this client's handoff before relying on the recorded checks.")
    if not available["sf"]:
        report["next_actions"].append("Install the official Salesforce CLI before live org work; context and the offline demo remain usable.")
    if not available["playwright"]:
        report["next_actions"].append("Browser testing needs Torque's browser extra and a configured browser runtime. See docs/installation.md.")
    if args.workspace:
        private_paths = ["workspace.json", "profile.md", ".torque", f"clients/{ws.slug_for(args.client)}" if args.client else "clients",
                         "initiatives"]
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
                a = access["antigravity_hook"]
                a_ok = a["verified"] and a["isolated"] and a["matcher_covers_tools"] and not a["disabled"]
                if claude_in_use:
                    state = ("hook command blocked a standalone probe; settings checked, host enforcement "
                             "not tested" if ok else "HOOK NOT IN FORCE")
                    print(f"AI access: build-only ({state})")
                    if not a["configured"]:
                        print("Antigravity hook: not registered in .agents/hooks.json")
                    elif a_ok:
                        print("Antigravity hook: blocked a standalone probe; host enforcement not tested")
                    else:
                        print("Antigravity hook: NOT IN FORCE")
                else:
                    state = ("hook command blocked a standalone probe; host enforcement not tested"
                             if a["configured"] and a_ok else "HOOK NOT IN FORCE")
                    print(f"AI access: build-only, host {in_use.name} ({state})")
                    if not h["configured"]:
                        print("Claude Code hook: not wired in .claude/settings.json")
                    elif ok:
                        print("Claude Code hook: blocked a standalone probe; settings checked, host enforcement "
                              "not tested")
                    else:
                        print("Claude Code hook: NOT IN FORCE")
            elif access["mode"] == "connected":
                connected = access["connected"]
                state = "ready" if connected["ready"] else "NOT READY"
                if not claude_in_use:
                    state = f"NOT CHECKED under {in_use.name}; the checks below are Claude Code's"
                print(f"AI access: connected, approval required ({connected['approval_verify']}; {state})")
                from . import doctor_connected
                doctor_connected.print_report(connected)
            else:
                print("AI access: full (build-only mode off)")
                h = access["hook"]
                if not h["configured"]:
                    print("Record protection: NOT IN FORCE (no torque.gate hook in .claude/settings.json)")
                elif h["verified"] and not h["disabled_by"]:
                    print("Record protection: hook command blocked a standalone record probe; host enforcement "
                          "not tested")
                else:
                    print("Record protection: NOT IN FORCE ("
                          + ("the hook blocks every call" if h["blocks_every_call"] else "see the next actions") + ")")
                a = access["antigravity_hook"]
                if a["configured"]:
                    if a["verified"] and a["isolated"] and a["matcher_covers_tools"] and not a["disabled"]:
                        print("Record protection (Antigravity): hook command blocked a standalone record probe; "
                              "host enforcement not tested")
                    else:
                        print("Record protection (Antigravity): NOT IN FORCE (see the next actions)")
                elif not claude_in_use:
                    print("Record protection (Antigravity): NOT IN FORCE (no torque.gate_antigravity hook in "
                          ".agents/hooks.json)")
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


def _utf8_output() -> None:
    """Windows gives a redirected stdout or stderr the ANSI code page, which cannot
    encode much of what notes, summaries and recipes hold, and an agent host reads
    the output as UTF-8. A console window and an explicit PYTHONIOENCODING stay as
    they are."""
    if os.name != "nt" or os.environ.get("PYTHONIOENCODING"):
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            if not stream.isatty() and (stream.encoding or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    global INVOCATION
    _utf8_output()
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
                from . import permissions
                # Full mode: the hook keeps tools from changing consent, approval and control records.
                hooks = [] if parsed.no_hook else permissions.wire_gate_hooks(path, antigravity=True)
                if parsed.json:
                    _print_json({"workspace": str(path), "org_calls": False, "hooks": hooks})
                else:
                    print(path)
                    for line in _hook_lines(hooks, "full"):
                        print(line)
                return 3 if any(row["status"] == "failed" for row in hooks) else 0
            elif parsed.action == "ai-access":
                root = ws.set_ai_access(parsed.path, parsed.mode, parsed.approval, parsed.verify,
                                        parsed.approver_uid, delegated=parsed.delegated,
                                        model_id=parsed.model_id)
                from . import permissions
                hooks, verified = [], []
                if parsed.mode != "connected" and not parsed.no_hook:
                    # Connected mode wires its hooks with `torque approval permissions --write --with-hooks`.
                    wired = permissions.wire_gate_hooks(root, antigravity=True)
                    hooks += wired
                    verified = _hook_verification(root, wired)
                shown = "connected (approval required)" if parsed.mode == "connected" else parsed.mode
                if parsed.json:
                    _print_json({"workspace": str(root), "ai_access": parsed.mode,
                                 **({"approval": parsed.approval} if parsed.approval else {}), "hooks": hooks})
                else:
                    print(shown)
                    for line in [*_hook_lines(hooks, parsed.mode), *verified]:
                        print(line)
                return 3 if any(row["status"] == "failed" for row in hooks) else 0
            elif parsed.action == "guarded-reads":
                ws.set_guarded_reads(parsed.path, parsed.value)
                print(f"guarded reads: {parsed.value}")
            elif parsed.action == "delegate":
                root = delegation.set_delegate(parsed.path, parsed.role, parsed.account, parsed.uid, parsed.kind)
                config = ws.load_workspace(root)[1]
                if parsed.json:
                    _print_json({"workspace": str(root), "delegates": config["delegates"]})
                else:
                    print(f"{parsed.role} delegate: {parsed.account} (uid {parsed.uid}, {parsed.kind})")
            else:
                return _upgrade(parsed)
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
        elif parsed.command == "guarded":
            from . import guarded
            return guarded.run(parsed)
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
        elif parsed.command == "initiative":
            from . import engagements as eng
            if parsed.action == "add":
                path = eng.add_initiative(parsed.workspace, parsed.name, parsed.owner)
                _print_json({"initiative_root": str(path), "org_calls": False}) if parsed.json else print(path)
            elif parsed.action == "list":
                rows = eng.list_engagements(parsed.workspace, "initiative")
                if parsed.json:
                    _print_json(rows)
                else:
                    for row in rows:
                        print(f"{row['slug']}: {row['name']} ({row['state']}, owner: {row['owner'] or 'not set'})")
                    if not rows:
                        print("No initiatives yet. Use torque initiative add NAME --workspace PATH.")
            else:
                config = (eng.load_initiative(parsed.workspace, parsed.name)[2] if parsed.action == "show"
                          else eng.set_state(parsed.workspace, parsed.name, parsed.state, reason=parsed.reason,
                                             review_date=parsed.review_date, outcome=parsed.outcome))
                if parsed.json:
                    _print_json(config)
                else:
                    print(f"{config['slug']}: {config['name']} ({config['state']}, owner: {config['owner'] or 'not set'})")
        elif parsed.command == "engagement":
            from . import engagements as eng
            rows = eng.list_engagements(parsed.workspace, parsed.kind)
            if parsed.json:
                _print_json(rows)
            else:
                for row in rows:
                    print(f"{row['kind']:<10} {row['slug']}: {row['name']} ({row['state']})")
        elif parsed.command == "change":
            return _change(parsed)
        elif parsed.command == "context":
            name, kind = _scope(parsed)
            context = ws.get_context(parsed.workspace, name, kind=kind)
            if parsed.json:
                _print_json(context)
            else:
                print(f"{context['workspace']['name']} / {context['client']['name']}")
                print(f"{'Initiative' if kind == 'initiative' else 'Client'} directory: {context['client_root']}")
                if kind == "client":
                    print(f"Configured org: {context['client'].get('org') or 'not specified'}")
                print(context["evidence_note"])
                for line in ws.record_problem_lines(context["record_problems"]):
                    print(line)
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
            name, kind = _scope(parsed)
            if parsed.action == "add":
                entry = ws.add_session(parsed.workspace, name, parsed.summary, parsed.status, parsed.evidence,
                                       kind=kind)
                if parsed.json:
                    _print_json(entry)
                else:
                    print(f"Recorded {entry['id']} [{entry['status']}, user-reported]")
            else:
                problems: list[dict] = []
                entries = ws.list_sessions(parsed.workspace, name, limit=None, kind=kind, problems=problems)
                if parsed.action == "show":
                    entry = next((e for e in entries if e["id"] == parsed.entry_id), None)
                    if entry is None:
                        raise ws.WorkspaceError("session entry was not found for the selected client"
                                                + ("; some records could not be read: "
                                                   + ", ".join(p["path"] for p in problems) if problems else ""))
                    entries = [entry]
                if parsed.json:
                    _print_json(entries[0] if parsed.action == "show" else entries)
                    if problems and parsed.action == "list":
                        print("\n".join(ws.record_problem_lines(problems)), file=sys.stderr)
                else:
                    for entry in entries:
                        print(f"{entry['id']} [{entry['status']}, user-reported] {entry['summary']}")
                        if entry["evidence_integrity"] in ("missing", "changed", "unavailable"):
                            print(f"Evidence: {entry['evidence_integrity']} since recording.")
                    if not entries:
                        print(f"No session entries for this {'initiative' if kind == 'initiative' else 'client'}.")
                    if parsed.action == "list":
                        for line in ws.record_problem_lines(problems):
                            print(line)
        elif parsed.command == "handoff":
            name, kind = _scope(parsed)
            body = ws.render_handoff(parsed.workspace, name, kind=kind)
            if parsed.output:
                output = ws.client_output_path(parsed.workspace, name, parsed.output, kind=kind)
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
