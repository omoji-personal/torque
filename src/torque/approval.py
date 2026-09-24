"""Per-write approval for connected mode.

The agent requests an approval for one exact write; the consultant grants it at
a real terminal after reading what it does; the gate consumes it once when the
agent runs that exact call. An approval binds the call, the files it deploys or
loads, the org alias and the org's 18-character ID, the client, the change and
a time window. The request file is untrusted: the grant derives everything it
signs from the request's command again.

Two ways to trust a granted approval (workspace.json "approval_verify"):
- "hmac" (tier 1): signed with a key in the consultant's home folder. The agent
  runs as the same OS account, so a script it writes can read that key and forge
  an approval. The gate and the generated permission rules keep recognized tools
  away from the key; that is all.
- "owner-uid" (tier 2, recommended): the consultant grants from a separate OS
  account. The gate accepts only approval files owned by that account and not
  writable by others, which the agent's account cannot create.
Design and limits: docs/connected-approval.md."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shlex
import sys
import time

from . import before_state, changes, consent, workspace as ws
from .connected_routes import classify_bash, is_simple
from .namespaces import find_namespaces

SCHEMA = "torque.approval/1"
REQUEST_SCHEMA = "torque.approval-request/1"
KINDS = ("command", "mcp", "browser")
TTL_SECONDS = {"command": 900, "mcp": 900, "browser": 1800}
SKEW = 60
PAYLOAD_FILE_CAP = 2000
PAYLOAD_BYTES_CAP = 20 * 1024 * 1024
PAYLOAD_WALK_CAP = 20000
# Flags whose value is a file or folder the command deploys, loads or runs.
PAYLOAD_FLAGS = ("-d", "--source-dir", "--metadata-dir", "-x", "--manifest", "-f", "--file", "--files", "--plan",
                 "--pre-destructive-changes", "--post-destructive-changes", "--sobject-tree-files",
                 "--apex-code-file")
COMPONENT_FLAGS = ("-m", "--metadata")
MIN_RECOVERY_CHARS = 40
MIN_PURPOSE_CHARS = 10
NONPRODUCTION = ("sandbox", "developer", "scratch")
REQUIRED = ("schema", "id", "request_id", "client", "change", "kind", "call_key", "org_alias", "org_id_18",
            "org_kind", "approver", "granted_at", "expires_at")
WRAPPER_WINDOW = 120
_ID_CHARS = set("0123456789abcdef")


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")


def _epoch(text: str) -> float:
    value = datetime.fromisoformat(text)
    if value.utcoffset() is None:
        raise ValueError("timestamp without a time zone")
    return value.timestamp()


def key_path() -> Path:
    """Tier 1 signing key: ~/.config/torque/approval.key (Windows: %APPDATA%\\torque)."""
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "torque" / "approval.key"
    return Path.home() / ".config" / "torque" / "approval.key"


def _key(create: bool) -> bytes | None:
    path = key_path()
    if path.is_file() and not path.is_symlink():
        if os.name != "nt":
            st = path.stat()
            if st.st_mode & 0o077 or st.st_uid != os.getuid():
                raise ws.WorkspaceError(f"{path} must be owned by you and readable by you only (chmod 600)")
        data = path.read_bytes()
        if len(data) < 32:
            raise ws.WorkspaceError(f"{path} is not a valid approval key")
        return data
    if not create:
        return None
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(secrets.token_bytes(32))
    return path.read_bytes()


def _canonical(record: dict) -> bytes:
    body = {k: v for k, v in record.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sign(record: dict, key: bytes) -> str:
    return "hmac-sha256:" + hmac.new(key, _canonical(record), hashlib.sha256).hexdigest()


def call_key_for_command(command: str) -> str:
    """The binding for a shell command: its exact text, outer whitespace trimmed."""
    return "sha256:" + hashlib.sha256(command.strip().encode()).hexdigest()


def call_key_for_mcp(tool_name: str, tool_input: dict) -> str:
    text = tool_name + "\n" + json.dumps(tool_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def _flag_items(argv: list[str], names) -> list[str]:
    out = []
    for i, tok in enumerate(argv):
        if tok in names and i + 1 < len(argv):
            out.append(argv[i + 1])
        elif tok.startswith("--") and "=" in tok and tok.split("=", 1)[0] in names:
            out.append(tok.split("=", 1)[1])
    return out


def _package_dirs(cwd: Path) -> list[Path]:
    project = cwd / "sfdx-project.json"
    try:
        data = json.loads(project.read_text(encoding="utf-8"))
        dirs = [d.get("path") for d in data.get("packageDirectories", []) if isinstance(d, dict)]
        return [cwd / d for d in dirs if isinstance(d, str) and d]
    except (OSError, ValueError, AttributeError):
        return [cwd / "force-app"]


class _TooLarge(Exception):
    pass


def _walk(root: Path, budget: list[int]) -> list[Path]:
    """Every file under root, following no links; raises _TooLarge past the walk cap."""
    if root.is_file():
        return [root]
    found: list[Path] = []
    stack = [root]
    while stack:
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            budget[0] -= 1
            if budget[0] < 0:
                raise _TooLarge()
            if entry.is_dir(follow_symlinks=False):
                stack.append(Path(entry.path))
            elif entry.is_file(follow_symlinks=False) or entry.is_symlink():
                found.append(Path(entry.path))
    return found


def payload_files(argv: list[str], cwd: Path) -> list[Path] | None:
    """The local files that decide what the command writes: named files and folders,
    the manifest, and the project files matching each named component. None when
    over the caps (the caller then requires the torque deploy route)."""
    cwd = Path(cwd)
    budget = [PAYLOAD_WALK_CAP]
    files: list[Path] = []
    try:
        for value in _flag_items(argv, PAYLOAD_FLAGS):
            files += _walk(cwd / value, budget)
        components = _flag_items(argv, COMPONENT_FLAGS)
        manifests = _flag_items(argv, before_state.MANIFEST_FLAGS)
        if components or manifests:
            for extra in ("sfdx-project.json", ".forceignore"):
                if (cwd / extra).is_file():
                    files.append(cwd / extra)
            try:
                named = before_state.deploy_components(argv, cwd)
            except ws.WorkspaceError:
                named = components
            needles = []
            everything = False
            for component in named:
                kind, _, name = component.partition(":")
                found = before_state._needles(component)
                if not name or "*" in name:
                    everything = True
                needles += found
                if name and "*" not in name:
                    needles.append("/" + name.split(".")[-1] + ".")
            for folder in _package_dirs(cwd):
                for path in _walk(folder, budget):
                    text = path.as_posix()
                    if everything or any(n in text for n in needles):
                        files.append(path)
    except _TooLarge:
        return None
    unique = list(dict.fromkeys(files))
    return None if len(unique) > PAYLOAD_FILE_CAP else unique


def payload_digest(argv: list[str], cwd: Path) -> tuple[str | None, int]:
    """(sha256 over each payload file's path relative to cwd and content hash, file
    count), or (None, 0) over the caps."""
    cwd = Path(cwd)
    files = payload_files(argv, cwd)
    if files is None:
        return None, 0
    total, lines = 0, []
    for path in files:
        try:
            if path.is_symlink():
                data = ("link:" + os.readlink(path)).encode()
            else:
                data = path.read_bytes()
        except OSError:
            data = b"<unreadable>"
        total += len(data)
        if total > PAYLOAD_BYTES_CAP:
            return None, 0
        try:
            name = path.relative_to(cwd).as_posix()
        except ValueError:
            name = path.as_posix()
        lines.append(f"{name}\0{hashlib.sha256(data).hexdigest()}\n")
    return "sha256:" + hashlib.sha256("".join(sorted(lines)).encode()).hexdigest(), len(files)


def _dirs(workspace, client, create: bool = True) -> dict[str, Path]:
    folder, _, _ = ws.load_client(workspace, client)
    base = ws._inside(folder, folder / "approvals")
    out = {"client": folder, "base": base}
    for name in ("requests", "granted", "consumed"):
        out[name] = ws._inside(folder, base / name)
        if create:
            out[name].mkdir(mode=0o700, parents=True, exist_ok=True)
    return out


def _resolver(resolve):
    if resolve is not None:
        return resolve
    from jsc_revert.org_detect import resolve_org
    return resolve_org


def _usable_consent(workspace, client) -> dict:
    item = consent.load_consent(workspace, client)
    problems = consent.consent_problems(item)
    if problems:
        raise ws.WorkspaceError("consent is not usable: " + "; ".join(problems))
    return item


def _org_identity(consent_item: dict, alias: str, resolve) -> tuple[str, str]:
    entry = consent.approved_org(consent_item, alias)
    if entry is None:
        raise ws.WorkspaceError(f"org {alias!r} is not in this client's consent")
    info = _resolver(resolve)(alias)
    if info is None:
        raise ws.WorkspaceError(f"could not resolve {alias!r} live; nothing was recorded")
    if info.org_id_18 != entry["org_id_18"]:
        raise ws.WorkspaceError(f"the org ID for {alias!r} is now {info.org_id_18}; the consent records "
                                f"{entry['org_id_18']}. The alias points at another org.")
    kind = info.detected_org_type if info.detected_org_type in NONPRODUCTION else "production"
    return info.org_id_18, kind


def _command_head_ok(argv: list[str]) -> bool:
    from . import gate as g
    head = g._basename(argv[0]) if argv else ""
    if g._is_sf_token(argv[0]) or head in ("torque", "jsc"):
        return True
    if g.PY_LAUNCHER_RE.match(head):
        module, _ = g._python_module(argv[1:])
        return module in g.TORQUE_MAIN_MODULES or bool(module and module.startswith("jsc_revert"))
    return False


def _derive_command(argv: list[str], org_alias: str, cwd: Path, extra_namespaces=()) -> dict:
    """Everything the approval binds for a command, derived from argv alone."""
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
        raise ws.WorkspaceError("give the exact write command after --")
    if not _command_head_ok(argv):
        raise ws.WorkspaceError("the command must start with sf, sfdx, torque or jsc (no variables or wrappers)")
    command = shlex.join(argv)
    routes = classify_bash(command)
    writes = [r for r in routes if r.kind == "org_write"]
    if not is_simple(command) or len(routes) != 1 or len(writes) != 1:
        kinds = ", ".join(sorted({r.kind for r in routes}))
        raise ws.WorkspaceError(f"a request covers exactly one write command with no chaining or redirection "
                                f"(this is: {kinds})")
    if writes[0].org != org_alias:
        raise ws.WorkspaceError(f"the command targets {writes[0].org!r}, not {org_alias!r}")
    digest, count = payload_digest(argv, cwd)
    if digest is None:
        raise ws.WorkspaceError(f"the files this command deploys exceed {PAYLOAD_FILE_CAP} files or "
                                f"{PAYLOAD_BYTES_CAP // (1024 * 1024)} MB; deploy a narrower source directory")
    return {"command": command, "call_key": call_key_for_command(command), "payload_digest": digest,
            "payload_files": count, "payload_argv": list(argv), "cwd": str(cwd),
            "namespaces": find_namespaces(argv, cwd, tuple(extra_namespaces)),
            "components": before_state.deploy_components(argv, cwd)}


def _derive_mcp(tool_name: str, tool_input: dict, org_alias: str) -> dict:
    from .connected_routes import classify
    if not isinstance(tool_name, str) or not tool_name.startswith("mcp__") or not isinstance(tool_input, dict):
        raise ws.WorkspaceError("an MCP request needs the tool name (mcp__server__tool) and its JSON input")
    routes = classify(tool_name, tool_input)
    writes = [r for r in routes if r.kind == "org_write"]
    if len(routes) != 1 or len(writes) != 1 or writes[0].org != org_alias:
        raise ws.WorkspaceError("an MCP request covers one Salesforce write tool call naming this org")
    text = json.dumps(tool_input, sort_keys=True, ensure_ascii=False)
    return {"command": f"{tool_name} {text}", "call_key": call_key_for_mcp(tool_name, tool_input),
            "payload_digest": None, "payload_files": 0, "payload_argv": None, "cwd": None,
            "namespaces": find_namespaces([text], Path(".")), "components": [],
            "mcp": {"tool_name": tool_name, "tool_input": tool_input}}


def _derive(req: dict, extra_namespaces=()) -> dict:
    if req.get("kind") == "command":
        cwd = Path(str(req.get("cwd") or ""))
        if not cwd.is_absolute() or not cwd.is_dir():
            raise ws.WorkspaceError("the request's working folder is missing")
        return _derive_command(req.get("payload_argv"), req.get("org_alias"), cwd, extra_namespaces)
    if req.get("kind") == "mcp":
        mcp = req.get("mcp") or {}
        return _derive_mcp(mcp.get("tool_name"), mcp.get("tool_input"), req.get("org_alias"))
    if req.get("kind") == "browser":
        purpose = str(req.get("purpose") or "").strip()
        minutes = req.get("browser_minutes")
        if len(purpose) < MIN_PURPOSE_CHARS or type(minutes) is not int or not 1 <= minutes <= 30:
            raise ws.WorkspaceError("a browser approval needs a purpose and 1 to 30 minutes")
        return {"command": f"browser: {purpose}", "call_key": None, "payload_digest": None, "payload_files": 0,
                "payload_argv": None, "cwd": None, "namespaces": [], "components": []}
    raise ws.WorkspaceError("unknown request kind")


def _extra_namespaces(workspace) -> tuple[str, ...]:
    config = ws.load_workspace(workspace)[1]
    extra = config.get("managed_namespaces")
    return tuple(n for n in extra if isinstance(n, str)) if isinstance(extra, list) else ()


def create_request(workspace, client, change_id, org_alias, *, argv=None, mcp=None, browser_minutes=None,
                   purpose=None, before_state_event=None, manual_recovery=None, validated_job=None,
                   cwd=None, resolve=None) -> dict:
    """Record a request (the agent may run this). Nothing is approved until the
    consultant grants it."""
    item = _usable_consent(workspace, client)
    changes.load_change(workspace, client, change_id)
    cwd = Path(os.path.realpath(str(cwd or os.getcwd())))
    if sum(x is not None and x != [] for x in (argv, mcp, browser_minutes)) != 1:
        raise ws.WorkspaceError("give exactly one of: a command after --, an MCP call, or --browser")
    if browser_minutes is not None:
        kind = "browser"
        req = {"kind": kind, "purpose": (purpose or "").strip(), "browser_minutes": browser_minutes,
               "org_alias": org_alias}
    elif mcp is not None:
        kind = "mcp"
        req = {"kind": kind, "mcp": {"tool_name": mcp[0], "tool_input": mcp[1]}, "org_alias": org_alias}
    else:
        kind = "command"
        req = {"kind": kind, "payload_argv": list(argv), "cwd": str(cwd), "org_alias": org_alias}
    derived = _derive(req, _extra_namespaces(workspace))
    org_id, org_kind = _org_identity(item, org_alias, resolve)
    if manual_recovery is not None and len(manual_recovery.strip()) < MIN_RECOVERY_CHARS:
        raise ws.WorkspaceError(f"a manual recovery path needs at least {MIN_RECOVERY_CHARS} characters")
    if before_state_event is not None:
        before_state.load_before_state(workspace, client, change_id, before_state_event)
    if validated_job is not None and (not isinstance(validated_job, str) or not validated_job.isalnum()):
        raise ws.WorkspaceError("the validated job must be a Salesforce ID")
    ident = "req-" + secrets.token_hex(6)
    record = {"schema": REQUEST_SCHEMA, "id": ident, "client": ws.slug_for(client), "change": change_id,
              **req, **derived, "org_id_18": org_id, "org_kind": org_kind,
              "before_state_event": before_state_event,
              "manual_recovery": manual_recovery.strip() if manual_recovery else None,
              "validated_job": validated_job, "created_at": _iso(time.time())}
    dirs = _dirs(workspace, client)
    ws._write_json(dirs["requests"] / f"{ident}.json", record)
    changes.append_approval_event(workspace, client, change_id, "approval_request",
                                  {"request_id": ident, "command": derived["command"], "request_kind": kind,
                                   "org_alias": org_alias, "org_id_18": org_id, "org_kind": org_kind,
                                   "validated_job": validated_job, "before_state_event": before_state_event,
                                   "manual_recovery": record["manual_recovery"]})
    return record


def _valid_id(value: str, prefix: str) -> bool:
    return (isinstance(value, str) and value.startswith(prefix) and len(value) == len(prefix) + 12
            and set(value[len(prefix):]) <= _ID_CHARS)


def load_request(workspace, client, request_id) -> dict:
    if not _valid_id(request_id, "req-"):
        raise ws.WorkspaceError("a request ID looks like req-0123456789ab")
    path = _dirs(workspace, client)["requests"] / f"{request_id}.json"
    if not path.is_file():
        raise ws.WorkspaceError(f"no request {request_id} for this client")
    req = ws._read_json(path)
    if req.get("schema") != REQUEST_SCHEMA or req.get("id") != request_id or req.get("client") != ws.slug_for(client):
        raise ws.WorkspaceError(f"invalid request file: {path}")
    return req


def _require_operator(presence) -> None:
    if presence is None:
        from .presence import operator_present as presence
    check = presence()
    if not check.ok:
        raise ws.WorkspaceError(f"approvals are granted by the consultant at a real terminal: {check.reason}")


def _denied(workspace, client, change_id, request_id) -> bool:
    events = changes.get_change(workspace, client, change_id)["events"]
    return any(e["kind"] == "approval_deny" and e.get("request_id") == request_id for e in events)


def _user() -> str:
    from jsc_revert.intent_marker import _current_user_name
    return _current_user_name()


def grant(workspace, client, request_id, *, new_components=(), presence=None, confirm=None, out=None,
          resolve=None, now=None) -> dict:
    """The consultant's grant, at a real terminal, after reading the call."""
    _require_operator(presence)
    out = out or sys.stdout
    req = load_request(workspace, client, request_id)
    change_id = req.get("change")
    changes.load_change(workspace, client, change_id)
    if _denied(workspace, client, change_id, request_id):
        raise ws.WorkspaceError(f"{request_id} was denied; ask for a new request")
    config = ws.load_workspace(workspace)[1]
    item = _usable_consent(workspace, client)
    derived = _derive(req, _extra_namespaces(workspace))
    org_id, org_kind = _org_identity(item, req["org_alias"], resolve)
    new_components = [c for c in new_components if isinstance(c, str) and c]
    before = None
    recovery = req.get("manual_recovery") if isinstance(req.get("manual_recovery"), str) else None
    if recovery is not None and len(recovery.strip()) < MIN_RECOVERY_CHARS:
        recovery = None
    if org_kind == "production" and req["kind"] == "browser":
        if not recovery:
            raise ws.WorkspaceError("a production browser window needs a written manual recovery path "
                                    f"(--manual-recovery, at least {MIN_RECOVERY_CHARS} characters)")
    elif org_kind == "production":
        if req.get("before_state_event"):
            before = before_state.load_before_state(workspace, client, change_id, req["before_state_event"])
            missing = [c for c in before_state.coverage(derived["components"], before) if c not in new_components]
            if missing:
                raise ws.WorkspaceError("the before-state does not cover: " + ", ".join(missing)
                                        + " (declare a new component with --new-component TYPE:NAME)")
        elif not recovery:
            raise ws.WorkspaceError("production needs an independent before-state (--before-state or "
                                    "--capture-before-*) or a written manual recovery path")
    kind = req["kind"]
    minutes = req.get("browser_minutes") if kind == "browser" else None
    ttl = min(TTL_SECONDS[kind], int(minutes) * 60) if minutes else TTL_SECONDS[kind]
    lines = [
        f"Client:      {req['client']}    Change: {change_id}",
        f"Org:         {req['org_alias']}  {org_id}  {org_kind.upper()}",
        f"Kind:        {kind}; valid {ttl // 60} minutes" + ("; single use" if kind != "browser" else
                                                             "; every browser action in the window"),
        f"Call:        {derived['command']}",
        f"Working in:  {derived['cwd'] or 'n/a'}",
        f"Components:  {', '.join(derived['components']) or 'not listed'}",
        f"New:         {', '.join(new_components) or 'none declared'}",
        f"Check-only:  {req.get('validated_job') or 'none recorded'}",
        "Before:      " + (f"{before['path']} ({len(before['files'])} files, captured {before['captured_at']})"
                           if before else recovery or ("not required" if org_kind != "production" else "n/a")),
        f"Namespaces:  {', '.join(derived['namespaces']) or 'none'}",
        f"Payload:     {derived['payload_digest'] or 'n/a'} ({derived['payload_files']} files)",
    ]
    if derived["namespaces"]:
        lines.append("Warning:     this call names managed-package components; check that this is intended.")
    for line in lines:
        out.write(printable(line) + "\n")
    out.flush()
    if confirm is None:
        from .presence import confirm_code as confirm
    if not confirm():
        raise ws.WorkspaceError("the confirmation code did not match; nothing was granted")
    t = now if now is not None else time.time()
    record = {"schema": SCHEMA, "id": "apr-" + secrets.token_hex(6), "request_id": request_id,
              "client": req["client"], "change": change_id, "kind": kind, "command": derived["command"],
              "call_key": derived["call_key"], "payload_digest": derived["payload_digest"],
              "payload_argv": derived["payload_argv"], "cwd": derived["cwd"], "org_alias": req["org_alias"],
              "org_id_18": org_id, "org_kind": org_kind,
              "before_state": ({"event_id": before["event_id"], "sha256": before["sha256"], "path": before["path"],
                                "captured_at": before["captured_at"]} if before else None),
              "manual_recovery": recovery, "validated_job": req.get("validated_job"),
              "new_components": new_components, "namespaces": derived["namespaces"],
              "approver": _user(), "approver_uid": os.getuid() if hasattr(os, "getuid") else None,
              "granted_at": _iso(t), "expires_at": _iso(t + ttl), "single_use": kind != "browser"}
    verify = config.get("approval_verify", "hmac")
    if verify == "owner-uid":
        if not hasattr(os, "getuid") or os.getuid() != config.get("approver_uid"):
            raise ws.WorkspaceError("this workspace takes approvals only from the approver account "
                                    f"(uid {config.get('approver_uid')}); grant from that account")
    else:
        record["signature"] = _sign(record, _key(create=True))
    dirs = _dirs(workspace, client)
    path = dirs["granted"] / f"{record['id']}.json"
    ws._write_json(path, record)
    if os.name != "nt":
        path.chmod(0o600 if verify == "hmac" else 0o644)
    try:
        _log_grant(workspace, client, record)
    except OSError:
        out.write("Note: the change record is not writable from this account; the grant is logged when used.\n")
    return record


def printable(text: str) -> str:
    """text with every non-printable character (control, escape, bidirectional
    formatting) shown as an escape, so the screen shows what will run."""
    return "".join(c if c.isprintable() or c == " " else c.encode("unicode_escape").decode("ascii")
                   for c in str(text))


def _log_grant(workspace, client, record: dict) -> None:
    changes.append_approval_event(workspace, client, record["change"], "approval_grant",
                                  {"request_id": record["request_id"], "approval_id": record["id"],
                                   "command": record["command"], "org_alias": record["org_alias"],
                                   "org_id_18": record["org_id_18"], "org_kind": record["org_kind"],
                                   "approver": record["approver"], "granted_at": record["granted_at"],
                                   "expires_at": record["expires_at"], "before_state": record["before_state"],
                                   "manual_recovery": record["manual_recovery"],
                                   "validated_job": record["validated_job"]})


def deny(workspace, client, request_id, reason, *, presence=None) -> dict:
    _require_operator(presence)
    if not isinstance(reason, str) or not reason.strip():
        raise ws.WorkspaceError("give a reason")
    req = load_request(workspace, client, request_id)
    return changes.append_approval_event(workspace, client, req["change"], "approval_deny",
                                         {"request_id": request_id, "reason": reason.strip(),
                                          "command": req.get("command"), "org_alias": req.get("org_alias")})


def _problem(record: dict, path: Path, config: dict, client: str, now: float) -> str:
    """Why a granted approval cannot be used; "" when it can."""
    if record.get("schema") != SCHEMA or any(k not in record for k in REQUIRED) \
            or record.get("kind") not in KINDS or not _valid_id(record.get("id"), "apr-") \
            or path.name != f"{record.get('id')}.json" or record.get("client") != client:
        return "malformed approval"
    verify = config.get("approval_verify", "hmac")
    st = path.lstat()
    if path.is_symlink():
        return "approval file is a link"
    if verify == "owner-uid":
        approver = config.get("approver_uid")
        if os.name == "nt" or type(approver) is not int or st.st_uid != approver or st.st_mode & 0o022:
            return "the approval file's owner is not the approver account, or others can write it"
    elif verify == "hmac":
        if os.name != "nt" and (st.st_mode & 0o077 or st.st_uid != os.getuid()):
            return "approval file must be mode 0600 and owned by this user"
        try:
            key = _key(create=False)
        except ws.WorkspaceError as exc:
            return str(exc)
        if key is None or not isinstance(record.get("signature"), str) \
                or not hmac.compare_digest(record["signature"], _sign(record, key)):
            return "approval signature does not verify"
    else:
        return "unknown approval verification in workspace.json"
    try:
        granted, expires = _epoch(record["granted_at"]), _epoch(record["expires_at"])
    except (TypeError, ValueError):
        return "malformed approval"
    if granted > now + SKEW:
        return "approval is dated in the future"
    if expires - granted > TTL_SECONDS[record["kind"]] + SKEW or expires < granted:
        return "approval window is longer than allowed"
    if now > expires:
        return "approval expired"
    return ""


def _granted(dirs: dict) -> list[tuple[Path, dict]]:
    out = []
    for path in sorted(dirs["granted"].glob("apr-*.json")):
        try:
            out.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return [(p, r) for p, r in out if isinstance(r, dict)]


def _activity(dirs: dict, entry: dict) -> None:
    """One line per allowed connected-mode org action, for the reviewer."""
    try:
        path = dirs["base"] / "activity.jsonl"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": _iso(time.time()), **entry}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def log_activity(workspace, client, entry: dict) -> None:
    _activity(_dirs(workspace, client), entry)


def _claim(dirs: dict, record: dict, now: float, session_id, tool_use_id) -> bool:
    marker = dirs["consumed"] / record["id"]
    try:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": _iso(now), "session_id": session_id, "tool_use_id": tool_use_id}))
    return True


def _log_use(workspace, client, record: dict, session_id, tool_use_id) -> None:
    try:
        events = changes.get_change(workspace, client, record["change"])["events"]
        if not any(e["kind"] == "approval_grant" and e.get("approval_id") == record["id"] for e in events):
            _log_grant(workspace, client, record)
        changes.append_approval_event(workspace, client, record["change"], "approval_consume",
                                      {"approval_id": record["id"], "request_id": record["request_id"],
                                       "command": record["command"], "org_alias": record["org_alias"],
                                       "org_id_18": record["org_id_18"], "org_kind": record["org_kind"],
                                       "session_id": session_id, "tool_use_id": tool_use_id})
    except (OSError, ws.WorkspaceError):
        pass


def _binding_problem(workspace, client, record: dict) -> str:
    """The approval's change must still load, and the consent must still name the
    approved org ID for its alias."""
    try:
        changes.load_change(workspace, client, record.get("change"))
    except (OSError, ws.WorkspaceError):
        return "the approval's change record is missing or invalid"
    try:
        entry = consent.approved_org(consent.load_consent(workspace, client), record.get("org_alias"))
    except (OSError, ws.WorkspaceError):
        entry = None
    if entry is None or entry.get("org_id_18") != record.get("org_id_18"):
        return "the client's consent no longer names the org ID this approval was granted for"
    return ""


def consume(workspace, client, call_key, org_alias, *, config, session_id=None, tool_use_id=None,
            cwd=None, now=None) -> tuple[bool, str]:
    """Use a matching granted approval once. Returns (True, approval ID) or (False, why)."""
    now = now if now is not None else time.time()
    slug = ws.slug_for(client)
    dirs = _dirs(workspace, client)
    reasons = []
    for path, record in _granted(dirs):
        if record.get("call_key") != call_key or record.get("org_alias") != org_alias \
                or record.get("kind") == "browser":
            continue
        problem = _problem(record, path, config, slug, now) or _binding_problem(workspace, client, record)
        if problem:
            reasons.append(problem)
            continue
        if record["kind"] == "command":
            here = os.path.realpath(str(cwd)) if cwd is not None else None
            if here is not None and here != record.get("cwd"):
                reasons.append(f"the approval is for a command run in {record.get('cwd')}, not {here}")
                continue
            digest, _ = payload_digest(record.get("payload_argv") or [], Path(str(record.get("cwd"))))
            if digest is None:
                return False, "the files this command deploys are too large for the gate to check"
            if digest != record.get("payload_digest"):
                reasons.append("the files this command deploys changed after the approval")
                continue
        if not _claim(dirs, record, now, session_id, tool_use_id):
            reasons.append("approval already used")
            continue
        _log_use(workspace, client, record, session_id, tool_use_id)
        _activity(dirs, {"action": "approved write", "approval_id": record["id"], "org_alias": org_alias,
                         "command": record["command"], "session_id": session_id, "tool_use_id": tool_use_id})
        return True, record["id"]
    return False, "; ".join(dict.fromkeys(reasons)) or "no granted approval matches this exact call"


def find_browser_approval(workspace, client, org_alias, *, config, now=None) -> dict | None:
    """A valid browser window for this org, with no side effect."""
    now = now if now is not None else time.time()
    slug = ws.slug_for(client)
    dirs = _dirs(workspace, client)
    for path, record in _granted(dirs):
        if record.get("kind") == "browser" and record.get("org_alias") == org_alias \
                and not _problem(record, path, config, slug, now) and not _binding_problem(workspace, client, record):
            return record
    return None


def note_browser_use(workspace, client, record: dict, *, tool_name=None, session_id=None, tool_use_id=None,
                     now=None) -> None:
    """Log one browser action in a window; its first use is also a change event."""
    now = now if now is not None else time.time()
    dirs = _dirs(workspace, client)
    if _claim(dirs, record, now, session_id, tool_use_id):
        _log_use(workspace, client, record, session_id, tool_use_id)
    _activity(dirs, {"action": "browser", "approval_id": record["id"], "org_alias": record["org_alias"],
                     "tool": tool_name, "session_id": session_id, "tool_use_id": tool_use_id})


def active_browser_approval(workspace, client, org_alias, *, config, now=None, session_id=None,
                            tool_use_id=None) -> dict | None:
    """A valid browser window for this org, with its use logged."""
    record = find_browser_approval(workspace, client, org_alias, config=config, now=now)
    if record is not None:
        note_browser_use(workspace, client, record, session_id=session_id, tool_use_id=tool_use_id, now=now)
    return record


def require(workspace, client, org_alias, argv, *, config, cwd=None, now=None) -> tuple[bool, str]:
    """For scripts: consume the approval for this exact command before running it."""
    if not argv:
        return False, "give the exact write command after --"
    return consume(workspace, client, call_key_for_command(shlex.join(argv)), org_alias, config=config,
                   cwd=cwd if cwd is not None else os.getcwd(), now=now)


def command_words(command: str) -> tuple[str, list[str]] | None:
    """("torque" or "jsc", the words after it) for a Torque route command."""
    from . import gate as g
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if not words:
        return None
    head = g._basename(words[0])
    if head in ("torque", "jsc"):
        return head, words[1:]
    if g.PY_LAUNCHER_RE.match(head):
        module, after = g._python_module(words[1:])
        if module in g.TORQUE_MAIN_MODULES:
            return "torque", words[1 + after:]
        if module and module.startswith("jsc_revert"):
            return "jsc", words[1 + after:]
    return None


def consumed_for_wrapper(workspace, client, invocation: tuple[str, list[str]], org_alias, *,
                         window=WRAPPER_WINDOW, now=None) -> dict | None:
    """The approval the gate consumed in the last `window` seconds for this exact
    Torque route invocation, marked so it serves one wrapper run."""
    now = now if now is not None else time.time()
    dirs = _dirs(workspace, client)
    head, words = invocation
    for _path, record in _granted(dirs):
        marker = dirs["consumed"] / str(record.get("id"))
        if record.get("org_alias") != org_alias or record.get("kind") != "command" or not marker.is_file():
            continue
        try:
            used = json.loads(marker.read_text(encoding="utf-8"))
            at = _epoch(used["at"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if now - at > window or used.get("wrapper"):
            continue
        if command_words(str(record.get("command"))) == (head, list(words)):
            used["wrapper"] = _iso(now)
            marker.write_text(json.dumps(used), encoding="utf-8")
            return record
    return None


PARENT_WINDOW = 1800


def approved_parent(workspace, client, approval_id, org_alias, *, window=PARENT_WINDOW, now=None) -> dict | None:
    """For a wrapper the revert executor starts: the approval its parent run verified
    (consumed and matched by consumed_for_wrapper) in the last `window` seconds."""
    now = now if now is not None else time.time()
    if not _valid_id(approval_id, "apr-"):
        return None
    dirs = _dirs(workspace, client)
    marker = dirs["consumed"] / approval_id
    path = dirs["granted"] / f"{approval_id}.json"
    try:
        used = json.loads(marker.read_text(encoding="utf-8"))
        record = json.loads(path.read_text(encoding="utf-8"))
        started = _epoch(used["wrapper"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if record.get("org_alias") != org_alias or now - started > window:
        return None
    return record


def list_approvals(workspace, client) -> list[dict]:
    dirs = _dirs(workspace, client)
    out = []
    for _path, record in _granted(dirs):
        out.append({**{k: record.get(k) for k in ("id", "request_id", "change", "kind", "command", "org_alias",
                                                  "org_kind", "granted_at", "expires_at", "approver")},
                    "used": (dirs["consumed"] / str(record.get("id"))).is_file()})
    return out


def list_requests(workspace, client) -> list[dict]:
    dirs = _dirs(workspace, client)
    out = []
    for path in sorted(dirs["requests"].glob("req-*.json")):
        try:
            req = ws._read_json(path)
        except ws.WorkspaceError:
            continue
        out.append({k: req.get(k) for k in ("id", "change", "kind", "command", "org_alias", "org_kind",
                                            "created_at")})
    return out


def approval_log(workspace, client, since: str | None = None) -> list[dict]:
    """Approval events across the client's changes, oldest first, for the reviewer's sample."""
    rows = []
    for change in changes.list_changes(workspace, client):
        item = changes.get_change(workspace, client, change["id"])
        jobs = [e for e in item["events"] if e["kind"] == "metadata_observation"]
        for event in item["events"]:
            if event["kind"] not in changes.APPROVAL_KINDS and event["kind"] != changes.BEFORE_STATE_KIND:
                continue
            if since and event["created_at"] < since:
                continue
            row = {"change": change["id"], **{k: event.get(k) for k in (
                "created_at", "kind", "request_id", "approval_id", "command", "org_alias", "org_id_18", "org_kind",
                "approver", "session_id", "tool_use_id", "reason")}}
            if event["kind"] == "approval_consume":
                row["later_observations"] = [
                    {"job_id": j.get("job_id"), "result": j.get("result")} for j in jobs
                    if j.get("target_org") == event.get("org_alias") and j["created_at"] >= event["created_at"]]
            rows.append(row)
    return sorted(rows, key=lambda r: r["created_at"])
