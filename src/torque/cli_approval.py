"""CLI verbs for connected mode: `approval`, `client consent` and `launch`.

The agent may run `approval request|status|list|log|require` and `client consent
show`. Everything that grants, denies, records consent, binds a session or writes
permission rules needs the consultant at a real terminal, and the gate denies it
to the agent."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from . import workspace as ws

AGENT_BINARY = "claude"


def _client_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", required=True, help="private workspace directory")
    parser.add_argument("--client", required=True, help="explicit client name or slug")


def register(sub) -> None:
    approval = sub.add_parser("approval", help="connected mode: request, grant and check per-write approvals")
    actions = approval.add_subparsers(dest="action", required=True)
    request = actions.add_parser("request", help="record a request for one exact write (the agent may run this)")
    _client_args(request)
    request.add_argument("--change", required=True, help="the change record this write belongs to")
    request.add_argument("--org", required=True, help="org alias; must be in the client's consent")
    before = request.add_mutually_exclusive_group()
    before.add_argument("--before-state", help="folder from an earlier retrieve or record export")
    before.add_argument("--capture-before-metadata", action="append", metavar="TYPE:NAME",
                        help="retrieve this component now as the before-state (repeatable)")
    before.add_argument("--capture-before-record", action="append", metavar="OBJECT:ID",
                        help="read this record now as the before-state (repeatable)")
    before.add_argument("--manual-recovery", help="a written recovery path (at least 40 characters)")
    before.add_argument("--capture-before", action="store_true",
                        help="capture the before-state now from --metadata TYPE:NAME or --record OBJECT:ID")
    request.add_argument("--metadata", action="append", default=[], metavar="TYPE:NAME",
                         help="with --capture-before: a component to retrieve (repeatable)")
    request.add_argument("--record", action="append", default=[], metavar="OBJECT:ID",
                         help="with --capture-before: a record to read (repeatable)")
    request.add_argument("--before-state-object", metavar="OBJECT",
                         help="with --before-state: the object a CSV record export holds")
    request.add_argument("--validated-job", help="ID of the check-only deploy that validated this change")
    request.add_argument("--browser", action="store_true", help="request a browser window instead of a command")
    request.add_argument("--minutes", type=int, default=30, help="browser window length (1 to 30)")
    request.add_argument("--purpose", help="what the browser work will change")
    request.add_argument("--mcp", metavar="TOOL", help="request one MCP tool call (mcp__server__tool)")
    request.add_argument("--input", help="the MCP call's input as JSON")
    request.add_argument("--json", action="store_true")
    grant = actions.add_parser("grant", help="consultant only, at a real terminal")
    grant.add_argument("request_id")
    _client_args(grant)
    grant.add_argument("--new-component", action="append", default=[], metavar="TYPE:NAME",
                       help="a component the deploy creates, so no before-state can hold it")
    grant.add_argument("--audit-trail", metavar="FILE",
                       help="Setup Audit Trail rows (sf data query --json output) to compare with the before-state")
    deny = actions.add_parser("deny", help="consultant only, at a real terminal")
    deny.add_argument("request_id")
    _client_args(deny)
    deny.add_argument("--reason", required=True)
    status = actions.add_parser("status", help="show one request and whether it was granted or used")
    status.add_argument("request_id")
    _client_args(status)
    status.add_argument("--json", action="store_true")
    listing = actions.add_parser("list", help="list requests and approvals")
    _client_args(listing)
    listing.add_argument("--json", action="store_true")
    log = actions.add_parser("log", help="approval events across the client's changes, for review")
    _client_args(log)
    log.add_argument("--since", help="ISO date or time")
    log.add_argument("--json", action="store_true")
    require = actions.add_parser("require", help="for scripts: use the approval for this exact command, exit 3 if none")
    _client_args(require)
    require.add_argument("--org", required=True)
    permissions = actions.add_parser("permissions", help="print (or, owner only, write) the host permission rules")
    permissions.add_argument("--workspace", required=True)
    permissions.add_argument("--write", action="store_true", help="merge into WORKSPACE/.claude/settings.json")
    launch = sub.add_parser("launch", help="connected mode: start an AI session bound to one client (owner only)")
    _client_args(launch)


def register_consent(client_sub) -> None:
    consent = client_sub.add_parser("consent", help="connected mode: the client's recorded agreement")
    actions = consent.add_subparsers(dest="consent_action", required=True)
    record = actions.add_parser("record", help="consultant only: record the written agreement")
    _client_args(record)
    record.add_argument("--agreed-on", required=True, help="YYYY-MM-DD")
    record.add_argument("--evidence", required=True, help="the signed agreement file; a copy is kept with its hash")
    record.add_argument("--data", action="append", required=True,
                        help="metadata, records, debug_logs or local_artifacts (repeatable)")
    record.add_argument("--org", action="append", required=True, help="an approved org alias (repeatable)")
    record.add_argument("--suspend-contact", action="append", default=[], help="who can suspend access")
    record.add_argument("--json", action="store_true")
    show = actions.add_parser("show")
    _client_args(show)
    show.add_argument("--json", action="store_true")
    sign_off = actions.add_parser("sign-off", help="consultant only: record the second reviewer's sign-off")
    _client_args(sign_off)
    sign_off.add_argument("--reviewer", required=True)
    suspend = actions.add_parser("suspend", help="consultant only: stop connected work for this client")
    _client_args(suspend)


def split_tail(args: list[str]) -> tuple[list[str], list[str] | None]:
    """Commands that take `-- COMMAND...`: the words after the first `--`."""
    wants = (args[:2] in (["approval", "request"], ["approval", "require"])) or args[:1] == ["launch"]
    if wants and "--" in args:
        index = args.index("--")
        return args[:index], args[index + 1:]
    return args, None


def _print(value) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def launch(workspace, client, extra: list[str], execvp=os.execvp, presence=None) -> int:
    """Bind a new AI session to one client: the hook process inherits TORQUE_CLIENT
    from this environment, and nothing the session runs can change it."""
    injected = presence is not None
    if presence is None:
        from .presence import operator_present as presence
    check = presence()
    if not check.ok:
        raise ws.WorkspaceError(f"launch a connected session yourself, at a real terminal: {check.reason}")
    if not injected:
        from .presence import confirm_code
        if not confirm_code():
            raise ws.WorkspaceError("the confirmation code did not match; nothing was started")
    from . import consent, gate
    root, config = ws.load_workspace(workspace)
    if gate._resolve_ai_access(config.get("ai_access"), config.get("approval")) != "connected":
        raise ws.WorkspaceError("launch is for a connected workspace; see docs/connected-approval.md")
    folder, _, client_config = ws.load_client(root, client)
    problems = consent.consent_problems(consent.load_consent(root, client))
    if problems:
        raise ws.WorkspaceError("consent is not usable: " + "; ".join(problems))
    os.environ["TORQUE_CLIENT"] = client_config["slug"]
    os.environ["TORQUE_WORKSPACE"] = str(folder)
    os.chdir(root)
    execvp(AGENT_BINARY, [AGENT_BINARY, *extra])
    return 0


def _request(p, tail) -> int:
    from . import approval, before_state, consent
    item = approval._usable_consent(p.workspace, p.client)
    metadata = list(p.capture_before_metadata or []) + (list(p.metadata) if p.capture_before else [])
    records = list(p.capture_before_record or []) + (list(p.record) if p.capture_before else [])
    if (p.metadata or p.record) and not p.capture_before:
        raise ws.WorkspaceError("--metadata and --record go with --capture-before")
    if p.capture_before and not (metadata or records):
        raise ws.WorkspaceError("--capture-before needs --metadata TYPE:NAME or --record OBJECT:ID")
    if metadata and records:
        raise ws.WorkspaceError("capture metadata or records for one request, not both")
    if records and "records" not in consent.data_allowed(item):
        raise ws.WorkspaceError("this client's consent does not cover record data; use --manual-recovery")
    # The org is checked live against the consent before anything is read from it.
    org_id, _ = approval._org_identity(item, p.org, None)
    before = None
    if p.before_state:
        before = before_state.import_before_state(p.workspace, p.client, p.change, Path(p.before_state),
                                                  sobject=p.before_state_object)
    elif metadata:
        before = before_state.capture_metadata(p.workspace, p.client, p.change, p.org, metadata, org_id_18=org_id)
    elif records:
        before = before_state.capture_records(p.workspace, p.client, p.change, p.org, records, org_id_18=org_id)
    mcp = None
    if p.mcp:
        try:
            mcp = (p.mcp, json.loads(p.input or "{}"))
        except ValueError as exc:
            raise ws.WorkspaceError(f"--input must be JSON: {exc}") from exc
    record = approval.create_request(p.workspace, p.client, p.change, p.org, argv=tail or None, mcp=mcp,
                                     browser_minutes=p.minutes if p.browser else None, purpose=p.purpose,
                                     before_state_event=before["event_id"] if before else None,
                                     manual_recovery=p.manual_recovery, validated_job=p.validated_job)
    if p.json:
        _print(record)
        return 0
    print(f"Request {record['id']} recorded in change {record['change']} ({record['org_alias']}, "
          f"{record['org_kind']}).")
    if before:
        print(f"Before-state: {before['path']} ({len(before['files'])} files).")
    print("Stop here. Ask the consultant to run, in their own terminal:")
    print(f"  torque approval grant {record['id']} --workspace {p.workspace} --client {p.client}")
    if record["kind"] == "command":
        print("After they say it is granted, run exactly this, from this folder:")
        print(f"  {approval.printable(record['command'])}")
    elif record["kind"] == "mcp":
        print("After they say it is granted, make exactly this MCP call:")
        print(f"  {approval.printable(record['command'])}")
    return 0


def _grant(p) -> int:
    from . import approval
    record = approval.grant(p.workspace, p.client, p.request_id, new_components=p.new_component,
                            report=approval.live_deploy_report, audit_trail=p.audit_trail)
    print(f"Granted {record['id']}, valid until {record['expires_at']}.")
    if record["kind"] == "browser":
        print(f"Browser changes in {record['org_alias']} are allowed until then.")
    else:
        print("The agent may now run exactly:")
        print(f"  {approval.printable(record['command'])}")
    return 0


def _status(p) -> int:
    from . import approval
    req = approval.load_request(p.workspace, p.client, p.request_id)
    granted = [a for a in approval.list_approvals(p.workspace, p.client) if a["request_id"] == p.request_id]
    view = {k: req.get(k) for k in ("id", "change", "kind", "command", "org_alias", "org_id_18", "org_kind",
                                    "created_at")}
    view["approvals"] = granted
    if p.json:
        _print(view)
        return 0
    print(f"{view['id']} ({view['kind']}) {view['org_alias']} {view['org_kind']}: {view['command']}")
    if not granted:
        print("Not granted yet.")
    for item in granted:
        print(f"Granted {item['id']} until {item['expires_at']}; {'used' if item['used'] else 'not used yet'}.")
    return 0


def _list(p) -> int:
    from . import approval
    view = {"requests": approval.list_requests(p.workspace, p.client),
            "approvals": approval.list_approvals(p.workspace, p.client)}
    if p.json:
        _print(view)
        return 0
    for req in view["requests"]:
        print(f"{req['id']} {req['created_at']} {req['org_alias']} {req['command']}")
    for item in view["approvals"]:
        print(f"{item['id']} for {item['request_id']} until {item['expires_at']} "
              f"({'used' if item['used'] else 'unused'})")
    if not view["requests"]:
        print("No approval requests for this client.")
    return 0


def _log(p) -> int:
    from . import approval
    rows = approval.approval_log(p.workspace, p.client, p.since)
    if p.json:
        _print(rows)
        return 0
    for row in rows:
        ident = row.get("approval_id") or row.get("request_id") or ""
        print(f"{row['created_at']} {row['kind']} {ident} {row.get('org_alias') or ''} "
              f"{row.get('command') or row.get('reason') or ''}".rstrip())
        for observation in row.get("later_observations") or []:
            link = ("linked: the approval's validated job" if observation.get("linked")
                    else "not linked to this approval (same org, later)")
            print(f"    later metadata observation: job {observation['job_id']} {observation['result']} ({link})")
    if not rows:
        print("No approval events.")
    return 0


def _require(p, tail) -> int:
    from . import approval
    config = ws.load_workspace(p.workspace)[1]
    ok, why = approval.require(p.workspace, p.client, p.org, tail or [], config=config)
    if not ok:
        print(f"Not approved: {why}", file=sys.stderr)
        return 3
    print(f"Approved by {why}")
    return 0


def _permissions(p) -> int:
    from . import permissions
    if p.write:
        path = permissions.write_settings(p.workspace)
        print(f"Wrote the connected-mode permission rules to {path}")
        return 0
    _print({"permissions": permissions.generate()})
    return 0


def run(parsed, tail: list[str] | None) -> int:
    if parsed.command == "launch":
        return launch(parsed.workspace, parsed.client, tail or [])
    if parsed.action == "request":
        return _request(parsed, tail)
    if parsed.action == "grant":
        return _grant(parsed)
    if parsed.action == "deny":
        from . import approval
        approval.deny(parsed.workspace, parsed.client, parsed.request_id, parsed.reason)
        print(f"Denied {parsed.request_id}.")
        return 0
    if parsed.action == "status":
        return _status(parsed)
    if parsed.action == "list":
        return _list(parsed)
    if parsed.action == "log":
        return _log(parsed)
    if parsed.action == "require":
        return _require(parsed, tail)
    return _permissions(parsed)


def run_consent(p) -> int:
    from . import consent
    action = p.consent_action
    if action == "record":
        item = consent.record_consent(p.workspace, p.client, p.agreed_on, p.evidence, p.data, p.org,
                                      p.suspend_contact)
    elif action == "sign-off":
        item = consent.sign_off(p.workspace, p.client, p.reviewer)
    elif action == "suspend":
        item = consent.suspend(p.workspace, p.client)
    else:
        item = consent.load_consent(p.workspace, p.client)
        if item is None:
            print("No consent record for this client.")
            return 0
    if getattr(p, "json", False):
        _print(item)
        return 0
    problems = consent.consent_problems(item)
    print(f"Consent for {item['client']}: {item['status']}; agreed on {item['agreed_on']}.")
    print("Orgs: " + ", ".join(f"{o['alias']} ({o['kind']}, {o['org_id_18']})" for o in item["approved_orgs"]))
    print("Data: " + ", ".join(item["data_allowed"]))
    print("Usable for connected work." if not problems else "Not usable: " + "; ".join(problems))
    return 0
