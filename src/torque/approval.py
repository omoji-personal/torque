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
import re
import os
from pathlib import Path
import secrets
import shlex
import sys
import time

from . import argv_flags, before_state, changes, consent, workspace as ws
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
# Flags whose value is a file or folder the command deploys, loads or runs (sf and
# legacy sfdx spellings). Multi-value flags and comma lists are read in full.
PAYLOAD_FLAGS = ("-d", "--source-dir", "--sourcepath", "-p", "--metadata-dir", "--deploydir", "-x", "--manifest",
                 "-f", "--file", "--files", "--plan", "--pre-destructive-changes", "--post-destructive-changes",
                 "--predestructivechanges", "--postdestructivechanges", "--sobject-tree-files",
                 "--sobjecttreefiles", "--apex-code-file", "--apexcodefile", "--csvfile", "--csv-file")
COMPONENT_FLAGS = ("-m", "--metadata")
# MCP input keys whose string value names a local file or folder the call sends.
MCP_PATH_KEY = re.compile(r"(dir|directory|path|paths|file|files|manifest|source|folder)$", re.IGNORECASE)
TORQUE_WRITE_WORDS = ("deploy", "data", "org", "recover", "revert")
MIN_RECOVERY_CHARS = 40
MIN_PURPOSE_CHARS = 10
NONPRODUCTION = ("sandbox", "developer", "scratch")
REQUIRED = ("schema", "id", "request_id", "client", "change", "kind", "command", "call_key", "command_sha256",
            "payload_digest", "payload_argv", "payload_check", "cwd", "org_alias", "org_id_18", "org_kind",
            "before_state", "manual_recovery", "validated_job", "approver", "approver_uid", "granted_at",
            "expires_at", "single_use")
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
    return argv_flags.values(argv, names, legacy=argv_flags.is_legacy(argv))


def _plan_files(plan: Path) -> list[Path]:
    """Data files a `sf data import tree` plan names, relative to the plan's folder."""
    try:
        data = json.loads(plan.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = data if isinstance(data, list) else []
    out = []
    for entry in entries:
        files = entry.get("files") if isinstance(entry, dict) else None
        for name in files if isinstance(files, list) else []:
            if isinstance(name, str) and name:
                out.append(plan.parent / name)
    return out


def named_payload(argv: list[str], cwd: Path) -> list[Path]:
    """The files and folders a command names directly, plus a tree-import plan's data files."""
    cwd = Path(cwd)
    named = [cwd / value for value in _flag_items(argv, PAYLOAD_FLAGS)]
    plans = [cwd / value for value in _flag_items(argv, ("--plan",) if argv_flags.is_legacy(argv)
                                                   else ("--plan", "-p"))]
    for plan in plans:
        if plan.is_file():
            named += _plan_files(plan)
    return list(dict.fromkeys(named))


def payload_problems(argv: list[str], cwd: Path) -> list[str]:
    """Named payload paths that do not exist or are links (refused, never hashed as empty)."""
    problems = []
    for path in named_payload(argv, cwd):
        inside = [parent for parent in path.parents if parent == Path(cwd) or Path(cwd) in parent.parents]
        if path.is_symlink() or any(parent.is_symlink() for parent in inside):
            problems.append(f"{path} is a link; name the real file")
        elif not path.exists():
            problems.append(f"{path} does not exist")
    files = payload_files(argv, cwd, capped=False) or []
    problems += [f"{path} is a link; payload files must be real files" for path in files if path.is_symlink()]
    return problems


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


def payload_files(argv: list[str], cwd: Path, capped: bool = True) -> list[Path] | None:
    """The local files that decide what the command writes: named files and folders,
    a tree-import plan's data files, the manifest, and the project files matching
    each named component. None when over the caps (capped only)."""
    cwd = Path(cwd)
    budget = [PAYLOAD_WALK_CAP if capped else 10 ** 9]
    files: list[Path] = []
    try:
        for path in named_payload(argv, cwd):
            files += _walk(path, budget)
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
                if not name or "*" in name:
                    everything = True
                    continue
                needles += ["/" + n for n in before_state._needles(component)]
                needles.append("/" + name.split(".")[-1] + ".")
            for folder in _package_dirs(cwd):
                for path in _walk(folder, budget):
                    text = path.as_posix()
                    if everything or any(n in text for n in needles):
                        files.append(path)
    except _TooLarge:
        return None
    unique = list(dict.fromkeys(files))
    return None if capped and len(unique) > PAYLOAD_FILE_CAP else unique


def payload_digest(argv: list[str], cwd: Path, capped: bool = True) -> tuple[str | None, int]:
    """(sha256 over each payload file's path relative to cwd and content hash, file
    count), or (None, 0) over the caps. A link is hashed as its target text and the
    content read through it."""
    cwd = Path(cwd)
    files = payload_files(argv, cwd, capped=capped)
    if files is None:
        return None, 0
    total, lines = 0, []
    for path in files:
        try:
            data = path.read_bytes()
            if path.is_symlink():
                data = ("link:" + os.readlink(path) + "\0").encode() + data
        except OSError:
            data = b"<unreadable>"
        total += len(data)
        if capped and total > PAYLOAD_BYTES_CAP:
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


def _torque_route(argv: list[str]) -> bool:
    words = command_words(shlex.join(argv))
    return bool(words and words[1] and words[1][0] in TORQUE_WRITE_WORDS)


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
    problems = payload_problems(argv, cwd)
    if problems:
        raise ws.WorkspaceError("the files this command uses cannot be bound: " + "; ".join(problems[:5]))
    digest, count = payload_digest(argv, cwd)
    check = "gate"
    if digest is None:
        if not _torque_route(argv):
            raise ws.WorkspaceError(f"the files this command deploys exceed {PAYLOAD_FILE_CAP} files or "
                                    f"{PAYLOAD_BYTES_CAP // (1024 * 1024)} MB, more than the gate checks in "
                                    "time; run it through the torque deploy (or data/org) route, whose "
                                    "wrapper checks the files itself")
        digest, count = payload_digest(argv, cwd, capped=False)
        check = "wrapper"
    key = call_key_for_command(command)
    return {"command": command, "call_key": key, "command_sha256": key, "payload_digest": digest,
            "payload_files": count, "payload_argv": list(argv), "payload_check": check, "cwd": str(cwd),
            "namespaces": find_namespaces(argv, cwd, tuple(extra_namespaces)),
            "components": before_state.write_components(argv, cwd)}


def _mcp_paths(value, depth: int = 0) -> list[str]:
    out = []
    if isinstance(value, dict) and depth < 4:
        for key, item in value.items():
            if isinstance(key, str) and MCP_PATH_KEY.search(key):
                out += [v for v in (item if isinstance(item, list) else [item]) if isinstance(v, str) and v]
            out += _mcp_paths(item, depth + 1)
    elif isinstance(value, list) and depth < 4:
        for item in value:
            out += _mcp_paths(item, depth + 1)
    return out


def _derive_mcp(tool_name: str, tool_input: dict, org_alias: str, cwd: Path) -> dict:
    from .connected_routes import classify
    if not isinstance(tool_name, str) or not tool_name.startswith("mcp__") or not isinstance(tool_input, dict):
        raise ws.WorkspaceError("an MCP request needs the tool name (mcp__server__tool) and its JSON input")
    routes = classify(tool_name, tool_input)
    writes = [r for r in routes if r.kind == "org_write"]
    if len(routes) != 1 or len(writes) != 1 or writes[0].org != org_alias:
        raise ws.WorkspaceError("an MCP request covers one Salesforce write tool call naming this org")
    paths = list(dict.fromkeys(_mcp_paths(tool_input)))
    payload_argv = ["mcp", "--source-dir", *paths] if paths else None
    digest, count = None, 0
    if payload_argv:
        problems = payload_problems(payload_argv, cwd)
        if problems:
            raise ws.WorkspaceError("the files this call sends cannot be bound: " + "; ".join(problems[:5]))
        digest, count = payload_digest(payload_argv, cwd)
        if digest is None:
            raise ws.WorkspaceError("the files this call sends are too large for the gate to check")
    text = json.dumps(tool_input, sort_keys=True, ensure_ascii=False)
    key = call_key_for_mcp(tool_name, tool_input)
    return {"command": f"{tool_name} {text}", "call_key": key, "command_sha256": key,
            "payload_digest": digest, "payload_files": count, "payload_argv": payload_argv,
            "payload_check": "gate", "cwd": str(cwd) if payload_argv else None,
            "namespaces": find_namespaces([text], Path(".")), "components": [],
            "mcp": {"tool_name": tool_name, "tool_input": tool_input}}


def _derive(req: dict, extra_namespaces=()) -> dict:
    if req.get("kind") in ("command", "mcp"):
        cwd = Path(str(req.get("cwd") or ""))
        if not cwd.is_absolute() or not cwd.is_dir():
            raise ws.WorkspaceError("the request's working folder is missing")
        if req["kind"] == "command":
            return _derive_command(req.get("payload_argv"), req.get("org_alias"), cwd, extra_namespaces)
        mcp = req.get("mcp") or {}
        return _derive_mcp(mcp.get("tool_name"), mcp.get("tool_input"), req.get("org_alias"), cwd)
    if req.get("kind") == "browser":
        purpose = str(req.get("purpose") or "").strip()
        minutes = req.get("browser_minutes")
        if len(purpose) < MIN_PURPOSE_CHARS or type(minutes) is not int or not 1 <= minutes <= 30:
            raise ws.WorkspaceError("a browser approval needs a purpose and 1 to 30 minutes")
        return {"command": f"browser: {purpose}", "call_key": None, "command_sha256": None, "payload_digest": None,
                "payload_files": 0, "payload_argv": None, "payload_check": None, "cwd": None, "namespaces": [],
                "components": []}
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
        req = {"kind": kind, "mcp": {"tool_name": mcp[0], "tool_input": mcp[1]}, "org_alias": org_alias,
               "cwd": str(cwd)}
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
                                   "command_sha256": derived["command_sha256"],
                                   "payload_digest": derived["payload_digest"],
                                   "org_alias": org_alias, "org_id_18": org_id, "org_kind": org_kind,
                                   "validated_job": validated_job, "before_state_event": before_state_event,
                                   "manual_recovery": record["manual_recovery"]})
    if record["manual_recovery"]:
        changes.add_note(workspace, client, change_id,
                         f"Manual recovery path for {ident} ({org_alias}): {record['manual_recovery']}",
                         kind="decision")
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


def _require_operator(presence, confirm=None, typed: bool = False) -> None:
    """A person at a real terminal outside the session; with `typed` (and no injected
    presence check), a typed code too."""
    injected = presence is not None
    if presence is None:
        from .presence import operator_present as presence
    check = presence()
    if not check.ok:
        raise ws.WorkspaceError(f"approvals are granted by the consultant at a real terminal: {check.reason}")
    if typed and (confirm is not None or not injected):
        if confirm is None:
            from .presence import confirm_code as confirm
        if not confirm():
            raise ws.WorkspaceError("the confirmation code did not match; nothing was changed")


def _denied(workspace, client, change_id, request_id) -> bool:
    events = changes.get_change(workspace, client, change_id)["events"]
    return any(e["kind"] == "approval_deny" and e.get("request_id") == request_id for e in events)


def _user() -> str:
    from jsc_revert.intent_marker import _current_user_name
    return _current_user_name()


def _audit_trail_rows(path) -> list[dict]:
    """Setup Audit Trail rows from `sf data query --json` output or a JSON list."""
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ws.WorkspaceError(f"cannot read the audit trail file {path}") from exc
    if isinstance(data, dict):
        data = (data.get("result") or {}).get("records") if isinstance(data.get("result"), dict) else data.get("records")
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def _sf_time(text) -> float | None:
    if not isinstance(text, str):
        return None
    value = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text.replace("Z", "+00:00"))
    try:
        return _epoch(value)
    except ValueError:
        return None


def _audit_changes(rows: list[dict], components: list[str], since: str) -> list[str]:
    start = _epoch(since)
    names = {c.partition(":")[2].split(".")[-1].split(":")[-1] for c in components if ":" in c}
    names.discard("")
    found = []
    for row in rows:
        at = _sf_time(row.get("CreatedDate"))
        text = str(row.get("Display") or row.get("Action") or "")
        if at is not None and at > start and any(n.casefold() in text.casefold() for n in names):
            found.append(f"{row.get('CreatedDate')} {text}")
    return found


def live_deploy_report(job_id: str, org: str) -> dict | None:
    """The check-only job's status, read live with `sf project deploy report`."""
    import subprocess
    try:
        done = subprocess.run(["sf", "project", "deploy", "report", "--job-id", job_id, "--target-org", org,
                               "--json"], capture_output=True, text=True, timeout=120)
        data = json.loads(done.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    result = data.get("result") if isinstance(data, dict) else None
    return result if isinstance(result, dict) else None


def grant(workspace, client, request_id, *, new_components=(), presence=None, confirm=None, out=None,
          resolve=None, now=None, report=None, audit_trail=None) -> dict:
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
            if not derived["components"]:
                raise ws.WorkspaceError("Torque cannot list what this write changes, so no before-state can be "
                                        "checked against it; ask for a new request with a written manual "
                                        "recovery path (--manual-recovery)")
            if before.get("org_id_18") and before["org_id_18"] != org_id:
                raise ws.WorkspaceError(f"the before-state was captured from another org ({before['org_id_18']}), "
                                        f"not {org_id}")
            if req.get("created_at") and _epoch(before["captured_at"]) > _epoch(req["created_at"]) + SKEW:
                raise ws.WorkspaceError("the before-state was captured after the request; it must come first")
            missing = [c for c in before_state.coverage(derived["components"], before) if c not in new_components]
            if missing:
                raise ws.WorkspaceError("the before-state does not cover: " + ", ".join(missing)
                                        + " (declare a new component with --new-component TYPE:NAME, or ask for "
                                        "a request with a manual recovery path)")
        elif not recovery:
            raise ws.WorkspaceError("production needs an independent before-state (--before-state or "
                                    "--capture-before-*) or a written manual recovery path")
    kind = req["kind"]
    minutes = req.get("browser_minutes") if kind == "browser" else None
    ttl = min(TTL_SECONDS[kind], int(minutes) * 60) if minutes else TTL_SECONDS[kind]
    job = req.get("validated_job")
    job_line = job or "none recorded"
    if job:
        result = report(job, req["org_alias"]) if report else None
        if report is None:
            job_line += " (result not read)"
        elif not result:
            job_line += " (result could not be read)"
        else:
            status = result.get("status") or ("Succeeded" if result.get("success") else "unknown")
            job_line += f" {status}" + ("" if result.get("checkOnly", True) else " (NOT a check-only job)")
    drift = _audit_changes(_audit_trail_rows(audit_trail), derived["components"], before["captured_at"]) \
        if audit_trail and before else []
    lines = [
        f"Client:      {req['client']}    Change: {change_id}",
        f"Org:         {req['org_alias']}  {org_id}  {org_kind.upper()}",
        f"Kind:        {kind}; valid {ttl // 60} minutes" + ("; single use" if kind != "browser" else
                                                             "; every browser action in the window"),
        f"Call:        {derived['command']}",
        f"Working in:  {derived['cwd'] or 'n/a'}",
        f"Components:  {', '.join(derived['components']) or 'not listed'}",
        f"New:         {', '.join(new_components) or 'none declared'}",
        f"Check-only:  {job_line}",
        "Before:      " + (f"{before['path']} ({len(before['files'])} files, {before.get('how') or 'recorded'}, "
                           f"captured {before['captured_at']}"
                           + (f" from {before['org_id_18']})" if before.get("org_id_18") else "; org not verified)")
                           if before else recovery or ("not required" if org_kind != "production" else "n/a")),
        f"Namespaces:  {', '.join(derived['namespaces']) or 'none'}",
        f"Payload:     {derived['payload_digest'] or 'n/a'} ({derived['payload_files']} files)",
    ]
    if derived["namespaces"]:
        lines.append("Warning:     this call names managed-package components; check that this is intended.")
    for row in drift:
        lines.append(f"Warning:     Setup changed after the before-state was captured: {row}")
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
              "call_key": derived["call_key"], "command_sha256": derived["command_sha256"],
              "payload_digest": derived["payload_digest"], "payload_check": derived["payload_check"],
              "payload_argv": derived["payload_argv"], "cwd": derived["cwd"], "org_alias": req["org_alias"],
              "org_id_18": org_id, "org_kind": org_kind,
              "before_state": ({"event_id": before["event_id"], "sha256": before["sha256"], "path": before["path"],
                                "captured_at": before["captured_at"], "how": before.get("how"),
                                "org_id_18": before.get("org_id_18"), "job": before.get("job")}
                               if before else None),
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
    except (OSError, ws.WorkspaceError):
        if verify != "owner-uid":
            path.unlink(missing_ok=True)
            raise ws.WorkspaceError("the grant could not be recorded in the change record; nothing was granted")
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
                                   "command": record["command"], "command_sha256": record.get("command_sha256"),
                                   "payload_digest": record.get("payload_digest"), "org_alias": record["org_alias"],
                                   "org_id_18": record["org_id_18"], "org_kind": record["org_kind"],
                                   "approver": record["approver"], "granted_at": record["granted_at"],
                                   "expires_at": record["expires_at"], "before_state": record["before_state"],
                                   "manual_recovery": record["manual_recovery"],
                                   "validated_job": record["validated_job"]})


def deny(workspace, client, request_id, reason, *, presence=None, confirm=None) -> dict:
    _require_operator(presence, confirm, typed=True)
    if not isinstance(reason, str) or not reason.strip():
        raise ws.WorkspaceError("give a reason")
    req = load_request(workspace, client, request_id)
    return changes.append_approval_event(workspace, client, req["change"], "approval_deny",
                                         {"request_id": request_id, "reason": reason.strip(),
                                          "command": req.get("command"), "command_sha256": req.get("command_sha256"),
                                          "org_alias": req.get("org_alias"), "org_id_18": req.get("org_id_18"),
                                          "org_kind": req.get("org_kind"), "approver": _user()})


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
        if not hasattr(os, "getuid"):
            return "owner-uid approvals are not supported on this platform"
        if type(approver) is not int or approver == os.getuid():
            return "tier 2 needs a separate approver account; approver_uid is this account"
        if st.st_uid != approver or st.st_mode & 0o022:
            return "the approval file's owner is not the approver account, or others can write it"
        folder = path.parent.stat()
        if folder.st_uid != approver or folder.st_mode & 0o022:
            return "approvals/granted must be owned by the approver account and writable only by it"
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
    """One line per allowed connected-mode org action, for the reviewer. Raises when
    it cannot be written: the caller then refuses the action."""
    path = dirs["base"] / "activity.jsonl"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": _iso(time.time()), **entry}, ensure_ascii=False) + "\n")


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
    """The approval_consume event (and a grant event the approver could not write).
    Raises when the change record cannot be written."""
    events = changes.get_change(workspace, client, record["change"])["events"]
    if not any(e["kind"] == "approval_grant" and e.get("approval_id") == record["id"] for e in events):
        _log_grant(workspace, client, record)
    changes.append_approval_event(workspace, client, record["change"], "approval_consume", {
        "approval_id": record["id"], "request_id": record["request_id"], "command": record["command"],
        "command_sha256": record.get("command_sha256"), "payload_digest": record.get("payload_digest"),
        "org_alias": record["org_alias"], "org_id_18": record["org_id_18"], "org_kind": record["org_kind"],
        "approver": record.get("approver"), "before_state": record.get("before_state"),
        "manual_recovery": record.get("manual_recovery"), "validated_job": record.get("validated_job"),
        "granted_at": record.get("granted_at"), "expires_at": record.get("expires_at"),
        "session_id": session_id, "tool_use_id": tool_use_id})


def _binding_problem(workspace, client, record: dict) -> str:
    """The approval's change must still load, and the client's consent must still be
    usable and name the approved org ID for its alias."""
    try:
        changes.load_change(workspace, client, record.get("change"))
    except (OSError, ws.WorkspaceError):
        return "the approval's change record is missing or invalid"
    try:
        item = consent.load_consent(workspace, client)
    except (OSError, ws.WorkspaceError):
        item = None
    problems = consent.consent_problems(item, client=ws.slug_for(client))
    if problems:
        return "consent is not usable: " + "; ".join(problems)
    entry = consent.approved_org(item, record.get("org_alias"))
    if entry is None or entry.get("org_id_18") != record.get("org_id_18"):
        return "the client's consent no longer names the org ID this approval was granted for"
    return ""


def _record_use(workspace, client, dirs: dict, record: dict, action: str, session_id, tool_use_id,
                extra: dict | None = None) -> str:
    """Write the audit record for a claimed approval; on failure release the claim and
    say why (the caller refuses the action)."""
    try:
        _log_use(workspace, client, record, session_id, tool_use_id)
        _activity(dirs, {"action": action, "approval_id": record["id"], "org_alias": record["org_alias"],
                         "command": record["command"], "session_id": session_id, "tool_use_id": tool_use_id,
                         **(extra or {})})
    except (OSError, ws.WorkspaceError) as exc:
        (dirs["consumed"] / record["id"]).unlink(missing_ok=True)
        return f"the approval's use could not be recorded in the change record ({exc}); nothing ran"
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
        if record.get("payload_argv") and record.get("payload_check") != "wrapper":
            digest, _ = payload_digest(record["payload_argv"], Path(str(record.get("cwd"))))
            if digest is None:
                return False, "the files this call uses are too large for the gate to check"
            if digest != record.get("payload_digest"):
                reasons.append("the files this call uses changed after the approval")
                continue
        if not _claim(dirs, record, now, session_id, tool_use_id):
            reasons.append("approval already used")
            continue
        failed = _record_use(workspace, client, dirs, record, "approved write", session_id, tool_use_id)
        if failed:
            return False, failed
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
        failed = _record_use(workspace, client, dirs, record, "browser window first use", session_id, tool_use_id,
                             {"tool": tool_name})
        if failed:
            raise ws.WorkspaceError(failed)
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


def _config(workspace) -> dict:
    return ws.load_workspace(workspace)[1]


def _consumed(dirs: dict, record: dict) -> tuple[Path, dict] | None:
    marker = dirs["consumed"] / str(record.get("id"))
    try:
        used = json.loads(marker.read_text(encoding="utf-8"))
        _epoch(used["at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return (marker, used) if isinstance(used, dict) else None


def _authentic(workspace, client, path: Path, record: dict, used: dict, config: dict) -> bool:
    """The approval verifies (signature or owner, window at its use) and is still bound."""
    slug = ws.slug_for(client)
    return not (_problem(record, path, config, slug, _epoch(used["at"]))
                or _binding_problem(workspace, client, record))


def _wrapper_matches(workspace, client, invocation, org_alias, *, window, now, config):
    dirs = _dirs(workspace, client)
    head, words = invocation
    for path, record in _granted(dirs):
        if record.get("org_alias") != org_alias or record.get("kind") != "command":
            continue
        found = _consumed(dirs, record)
        if found is None:
            continue
        marker, used = found
        if now - _epoch(used["at"]) > window or used.get("wrapper"):
            continue
        if command_words(str(record.get("command"))) != (head, list(words)):
            continue
        if not _authentic(workspace, client, path, record, used, config):
            continue
        yield path, record, marker, used


def consumed_for_wrapper(workspace, client, invocation: tuple[str, list[str]], org_alias, *,
                         window=WRAPPER_WINDOW, now=None, config=None) -> dict | None:
    """The approval the gate consumed in the last `window` seconds for this exact
    Torque route invocation, authenticated again, with its files checked again (in
    full, with no gate time budget), marked so it serves one wrapper run."""
    now = now if now is not None else time.time()
    config = config if config is not None else _config(workspace)
    for _path, record, marker, used in _wrapper_matches(workspace, client, invocation, org_alias, window=window,
                                                          now=now, config=config):
        if record.get("payload_argv"):
            digest, _ = payload_digest(record["payload_argv"], Path(str(record.get("cwd"))), capped=False)
            if digest != record.get("payload_digest"):
                continue
        used["wrapper"] = _iso(now)
        marker.write_text(json.dumps(used), encoding="utf-8")
        return record
    return None


MAX_RELEASES = 3


def release_for_retry(workspace, client, invocation: tuple[str, list[str]], org_alias, *,
                      window=WRAPPER_WINDOW, now=None, config=None) -> bool:
    """After the wrapper could not resolve the org (nothing ran), return the approval
    the gate just consumed for this exact invocation, so the same command can be run
    again inside its window. At most three times per approval; each release is logged."""
    now = now if now is not None else time.time()
    config = config if config is not None else _config(workspace)
    dirs = _dirs(workspace, client)
    for _path, record, marker, used in _wrapper_matches(workspace, client, invocation, org_alias, window=window,
                                                          now=now, config=config):
        released = sorted(dirs["consumed"].glob(record["id"] + ".released-*"))
        if len(released) >= MAX_RELEASES:
            return False
        os.replace(marker, dirs["consumed"] / f"{record['id']}.released-{len(released) + 1}")
        try:
            _activity(dirs, {"action": "released after the org could not be resolved", "approval_id": record["id"],
                             "org_alias": org_alias})
        except OSError:
            pass
        return True
    return False


PARENT_WINDOW = 1800


def authorize_child(workspace, client, approval_id, child_words: list[str]) -> None:
    """The revert executor names the one wrapper command it will start for the
    approval its own run verified."""
    if not _valid_id(approval_id, "apr-"):
        raise ws.WorkspaceError("invalid approval ID")
    dirs = _dirs(workspace, client)
    marker = dirs["consumed"] / approval_id
    used = json.loads(marker.read_text(encoding="utf-8"))
    if not used.get("wrapper"):
        raise ws.WorkspaceError("the approval was not verified by the parent run")
    used["child"] = list(child_words)
    marker.write_text(json.dumps(used), encoding="utf-8")


def approved_parent(workspace, client, approval_id, org_alias, invocation=None, *, window=PARENT_WINDOW,
                    now=None, config=None) -> dict | None:
    """For the wrapper the revert executor starts: the approval its parent verified in
    the last `window` seconds, authenticated again, for exactly the command the parent
    named, used once."""
    now = now if now is not None else time.time()
    if not _valid_id(approval_id, "apr-"):
        return None
    config = config if config is not None else _config(workspace)
    dirs = _dirs(workspace, client)
    path = dirs["granted"] / f"{approval_id}.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    found = _consumed(dirs, record) if isinstance(record, dict) else None
    if found is None:
        return None
    marker, used = found
    try:
        started = _epoch(used["wrapper"])
    except (KeyError, TypeError, ValueError):
        return None
    if record.get("org_alias") != org_alias or now - started > window or used.get("child_used"):
        return None
    if invocation is None or used.get("child") != list(invocation[1]):
        return None
    if not _authentic(workspace, client, path, record, used, config):
        return None
    used["child_used"] = _iso(now)
    marker.write_text(json.dumps(used), encoding="utf-8")
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
                # Linked only by job ID (the validated job a quick deploy promotes); other
                # observations on the org after the use are listed as unlinked.
                row["validated_job"] = event.get("validated_job")
                row["later_observations"] = [
                    {"job_id": j.get("job_id"), "result": j.get("result"),
                     "linked": bool(event.get("validated_job")) and j.get("job_id") == event.get("validated_job")}
                    for j in jobs
                    if j.get("target_org") == event.get("org_alias") and j["created_at"] >= event["created_at"]]
            rows.append(row)
    return sorted(rows, key=lambda r: r["created_at"])
