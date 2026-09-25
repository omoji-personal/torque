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
    grant = actions.add_parser("grant", help="consultant at a real terminal, or the delegated approver (--delegated)")
    grant.add_argument("request_id")
    _client_args(grant)
    grant.add_argument("--new-component", action="append", default=[], metavar="TYPE:NAME",
                       help="a component the deploy creates, so no before-state can hold it")
    grant.add_argument("--audit-trail", metavar="FILE",
                       help="Setup Audit Trail rows (sf data query --json output) to compare with the before-state")
    grant.add_argument("--delegated", action="store_true",
                       help="the workspace's delegated approver is granting (tier 2, non-production orgs only)")
    grant.add_argument("--model-id", help="delegated only: the AI approver's model identifier")
    grant.add_argument("--request-sha256", help="bind this grant to the reviewed request_sha256 from "
                                                "`approval show --json` (delegated: required)")
    grant.add_argument("--payload-digest", help="bind this grant to the reviewed payload.digest from "
                                                "`approval show --json` (\"none\" when it has none; "
                                                "delegated: required)")
    grant.add_argument("--idempotency-key", help="delegated: a retry with this same key, request, "
                                                 "--request-sha256 and --payload-digest returns the earlier "
                                                 "grant instead of making a new one (8 to 128 letters, digits "
                                                 "and : . _ -)")
    grant.add_argument("--json", action="store_true",
                       help="print the approval record; the review screen goes to stderr")
    lookup = actions.add_parser("lookup", help="find an earlier delegated grant by its idempotency key")
    _client_args(lookup)
    lookup.add_argument("--idempotency-key", required=True, help="the key an earlier `grant --idempotency-key` used")
    lookup.add_argument("--json", action="store_true")
    deny = actions.add_parser("deny", help="consultant at a real terminal, or the delegated approver (--delegated)")
    deny.add_argument("request_id")
    _client_args(deny)
    deny.add_argument("--reason", required=True)
    deny.add_argument("--delegated", action="store_true",
                      help="the workspace's delegated approver is denying (tier 2, non-production only)")
    deny.add_argument("--reason-class", help="delegated only, required: 2 to 48 lowercase letters, digits "
                                             "and hyphens (for example: manifest-deny)")
    deny.add_argument("--model-id", help="delegated only: the AI approver's model identifier")
    deny.add_argument("--json", action="store_true", help="delegated only: print the denial record")
    status = actions.add_parser("status", help="show one request and whether it was granted or used")
    status.add_argument("request_id")
    _client_args(status)
    status.add_argument("--wait", type=int, metavar="SECONDS",
                        help="poll (read-only, no busy loop) until the request is granted, denied or expired, "
                             "or SECONDS elapse; exits granted 0, denied 20, expired 21, timeout 22, usage or "
                             "workspace errors 2 (0 to 3600)")
    status.add_argument("--json", action="store_true")
    show = actions.add_parser("show", help="the normalized request view an automated approver reads")
    show.add_argument("request_id")
    _client_args(show)
    show.add_argument("--json", action="store_true")
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
    permissions.add_argument("--unattended", action="store_true",
                             help="the unattended profile: no ask rule for a route the gate itself decides")
    permissions.add_argument("--with-hooks", action="store_true",
                             help="with --write: also wire the torque.gate hook (PreToolUse, PostToolUse, "
                                  "PostToolUseFailure)")
    permissions.add_argument("--delegated", action="store_true",
                             help="the workspace's setup delegate is running this, not the consultant")
    permissions.add_argument("--model-id", help="delegated only: the AI setup delegate's model identifier")
    permissions.add_argument("--hook-python",
                             help="with --with-hooks: the interpreter path baked into the gate hook command "
                                  "(default: this process's own sys.executable; a delegated write should name "
                                  "one the different agent account can actually execute)")
    binding = actions.add_parser("launch-binding", help="the delegated approver: a single-use binding for one "
                                                        "unattended `torque launch --delegated`")
    _client_args(binding)
    binding.add_argument("--model-id", help="an AI approver's model identifier (required for an AI delegate)")
    binding.add_argument("--minutes", type=int, default=10, help="how long the binding stays usable (1 to 15)")
    binding.add_argument("--json", action="store_true", help="print the binding record")
    launch = sub.add_parser("launch", help="connected mode: start an AI session bound to one client (owner at a "
                                           "real terminal, or --delegated with the approver's launch binding)")
    _client_args(launch)
    launch.add_argument("--delegated", action="store_true",
                        help="unattended: claim the delegated approver's launch binding instead of the presence check "
                             "(tier 2 only)")
    launch.add_argument("--binding", metavar="lnk-ID", help="with --delegated: the binding from "
                                                            "`torque approval launch-binding`")


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
    record.add_argument("--delegated", action="store_true",
                        help="the workspace's setup delegate is running this, not the consultant")
    record.add_argument("--model-id", help="delegated only: the AI reviewer's model identifier")
    record.add_argument("--json", action="store_true")
    show = actions.add_parser("show")
    _client_args(show)
    show.add_argument("--json", action="store_true")
    sign_off = actions.add_parser("sign-off", help="consultant only: record the second reviewer's sign-off")
    _client_args(sign_off)
    sign_off.add_argument("--reviewer", required=True)
    sign_off.add_argument("--delegated", action="store_true",
                          help="the workspace's setup delegate is running this, not the consultant")
    sign_off.add_argument("--model-id", help="delegated only: the AI reviewer's model identifier")
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


def launch(workspace, client, extra: list[str], execvp=os.execvp, presence=None, *, delegated=False, binding=None,
           env=None, ancestors=None) -> int:
    """Bind a new AI session to one client through a launch record for this process
    (exec keeps the pid): after the consultant's presence check and code
    (unchanged), or, with `delegated`, by claiming a single-use launch binding the
    workspace's delegated approver wrote. `extra` passes through to `claude`
    unchanged (for example `-p --input-format stream-json`). The hook process
    inherits TORQUE_CLIENT, TORQUE_WORKSPACE and TORQUE_LAUNCH from this
    environment; the gate binds only from the record TORQUE_LAUNCH names."""
    from . import consent, gate, launch as launches
    if delegated:
        from .delegation import Refusal
        if not binding:
            raise ws.WorkspaceError("--delegated needs --binding lnk-... from `torque approval launch-binding`")
        # Every presence-free check before anything else is read (agent-session,
        # tier 2, approver delegate, launching account); claim_binding repeats them.
        launches._tier2_config(workspace, env=env, ancestors=ancestors)
        flag = launches.launch_flag_problem(extra)
        if flag:
            raise Refusal("launch-flag-refused", f"a delegated launch does not pass {flag} to claude: only "
                                                 "the allowlisted options go through (R49)")
        variable = launches.launch_env_problem(os.environ)
        if variable:
            raise Refusal("launch-flag-refused", f"a delegated launch does not run with {variable} set; unset it "
                                                 "and launch again")
    else:
        if binding:
            raise ws.WorkspaceError("--binding goes with --delegated")
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
    root, config = ws.load_workspace(workspace)
    if gate._resolve_ai_access(config.get("ai_access"), config.get("approval")) != "connected":
        raise ws.WorkspaceError("launch is for a connected workspace; see docs/connected-approval.md")
    folder, _, client_config = ws.load_client(root, client)
    problems = consent.consent_problems(consent.load_consent(root, client))
    if problems:
        if delegated:
            raise Refusal("consent-unusable", "consent is not usable: " + "; ".join(problems))
        raise ws.WorkspaceError("consent is not usable: " + "; ".join(problems))
    record = (launches.claim_binding(root, client, binding, env=env, ancestors=ancestors) if delegated
              else launches.write_launch_record(root, client, "human"))
    os.environ["TORQUE_CLIENT"] = client_config["slug"]
    os.environ["TORQUE_WORKSPACE"] = str(folder)
    os.environ["TORQUE_LAUNCH"] = record["id"]
    if delegated:
        for name in launches.REFUSED_ENV:
            os.environ.pop(name, None)
    os.chdir(root)
    execvp(AGENT_BINARY, [AGENT_BINARY, *extra])
    return 0


def _launch_binding(p) -> int:
    from . import launch as launches
    record = launches.create_binding(p.workspace, p.client, model_id=p.model_id, minutes=p.minutes)
    if p.json:
        _print(record)
        return 0
    print(f"Launch binding {record['id']} for {record['client']}, single use, valid until {record['expires_at']}.")
    print("Start the session with:")
    print(f"  torque launch --workspace {p.workspace} --client {p.client} --delegated --binding {record['id']} "
          "-- CLAUDE OPTIONS")
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
    if not p.delegated and p.model_id is not None:
        raise ws.WorkspaceError("--model-id goes with --delegated")
    # F6: with --json, stdout carries only the record; the review screen goes to stderr.
    out = sys.stderr if p.json else None
    if p.delegated:
        record = approval.grant(p.workspace, p.client, p.request_id, new_components=p.new_component, out=out,
                                delegated=True, model_id=p.model_id, request_sha256=p.request_sha256,
                                payload_digest=p.payload_digest, idempotency_key=p.idempotency_key)
    else:
        # D6: the owner may also name the reviewed request's SHA-256 and payload digest
        # (from `approval show --json`), binding this grant to exactly what was reviewed.
        record = approval.grant(p.workspace, p.client, p.request_id, new_components=p.new_component, out=out,
                                report=approval.live_deploy_report, audit_trail=p.audit_trail,
                                request_sha256=p.request_sha256, payload_digest=p.payload_digest,
                                idempotency_key=p.idempotency_key)
    if p.json:
        _print(record)
        return 0
    print(f"Granted {record['id']}, valid until {record['expires_at']}.")
    if record["kind"] == "browser":
        print(f"Browser changes in {record['org_alias']} are allowed until then.")
    else:
        print("The agent may now run exactly:")
        print(f"  {approval.printable(record['command'])}")
    return 0


def _lookup(p) -> int:
    """D7: find an earlier delegated grant by its idempotency key. Exit 0 and the
    record when one is found, exit 1 and nothing on stdout when none is."""
    from . import approval
    record = approval.find_by_idempotency_key(p.workspace, p.client, p.idempotency_key)
    if record is None:
        return 1
    if p.json:
        _print(record)
        return 0
    print(f"{record['id']} for {record['request_id']}, granted {record['granted_at']}, "
          f"until {record['expires_at']}.")
    return 0


def _wait_line(state: str, detail: dict) -> str:
    if state == "granted":
        return f"granted {detail.get('approval_id')}"
    if state == "denied":
        return f"denied ({detail.get('reason_class')}): {detail.get('reason')}"
    if state == "expired":
        return f"expired: {detail.get('why')}"
    return f"timeout after {detail.get('waited_seconds')} s; wait again, do not write"


def _status(p) -> int:
    from . import approval
    if p.wait is not None:
        from . import delegation
        try:
            state, detail = approval.wait_for_decision(p.workspace, p.client, p.request_id, p.wait)
        except delegation.Refusal as exc:
            # D10 / spec requirement 21: --wait's own exit contract is granted 0,
            # denied 20, expired 21, timeout 22, usage or workspace errors 2. A
            # Refusal here (an R46 violation, an unreadable denial file, no
            # approver delegate) is none of those four states; report it as a
            # plain workspace error (exit 2, via cli.main's generic handler)
            # rather than through the grant/deny verbs' own exit-3 refusal
            # convention, and never let it be read as granted.
            raise ws.WorkspaceError(str(exc)) from exc
        view = {"request_id": p.request_id, "state": state, "detail": detail}
        if p.json:
            _print(view)
        else:
            print(_wait_line(state, detail))
        return approval.WAIT_CODES[state]
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


def _show(p) -> int:
    from . import approval
    view = approval.request_view(p.workspace, p.client, p.request_id)
    if p.json:
        _print(view)
        return 0
    for line in view["screen"]:
        print(line)
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
    profile = "unattended" if p.unattended else "interactive"
    if not p.write and (p.with_hooks or p.delegated or p.model_id or p.hook_python):
        raise ws.WorkspaceError("--with-hooks, --delegated, --model-id and --hook-python need --write")
    if p.write:
        path = permissions.write_settings(p.workspace, profile=profile, with_hooks=p.with_hooks,
                                          delegated=p.delegated, model_id=p.model_id, hook_python=p.hook_python)
        print(f"Wrote the connected-mode permission rules to {path}")
        return 0
    _print({"permissions": permissions.generate(profile)})
    return 0


def run(parsed, tail: list[str] | None) -> int:
    if parsed.command == "launch":
        if parsed.delegated or parsed.binding:
            # launch() checks the --delegated/--binding pairing.
            return launch(parsed.workspace, parsed.client, tail or [], delegated=parsed.delegated,
                          binding=parsed.binding)
        return launch(parsed.workspace, parsed.client, tail or [])
    if parsed.action == "launch-binding":
        return _launch_binding(parsed)
    if parsed.action == "request":
        return _request(parsed, tail)
    if parsed.action == "grant":
        return _grant(parsed)
    if parsed.action == "lookup":
        return _lookup(parsed)
    if parsed.action == "deny":
        from . import approval
        if parsed.delegated:
            record = approval.deny_delegated(parsed.workspace, parsed.client, parsed.request_id,
                                             parsed.reason_class, model_id=parsed.model_id, reason=parsed.reason)
            if parsed.json:
                _print(record)
                return 0
            print(f"Denied {record['id']} for {record['request_id']} ({record['reason_class']}).")
            return 0
        if parsed.reason_class is not None or parsed.model_id is not None or parsed.json:
            raise ws.WorkspaceError("--reason-class, --model-id and --json go with --delegated")
        approval.deny(parsed.workspace, parsed.client, parsed.request_id, parsed.reason)
        print(f"Denied {parsed.request_id}.")
        return 0
    if parsed.action == "status":
        return _status(parsed)
    if parsed.action == "show":
        return _show(parsed)
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
                                      p.suspend_contact, delegated=p.delegated, model_id=p.model_id)
    elif action == "sign-off":
        item = consent.sign_off(p.workspace, p.client, p.reviewer, delegated=p.delegated, model_id=p.model_id)
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
