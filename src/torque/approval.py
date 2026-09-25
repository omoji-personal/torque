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
import stat
import sys
import time

from . import argv_flags, before_state, changes, consent, delegation, workspace as ws
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
PAYLOAD_FLAGS = ("--recovery-snapshot", "-d", "--source-dir", "--sourcepath", "-p", "--metadata-dir", "--deploydir", "-x", "--manifest",
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
            "before_state", "manual_recovery", "validated_job", "approver", "approver_uid", "approver_kind",
            "approver_model", "delegated", "granted_at", "expires_at", "single_use")
WRAPPER_WINDOW = 120
_ID_CHARS = set("0123456789abcdef")
REQUEST_TTL = 3600
# An idempotency key a caller supplies (task D7): 8 to 128 letters, digits and : . _ -,
# starting with a letter or digit.
IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._-]{7,127}\Z")
VIEW_SCHEMA = "torque.approval-request-view/1"
DENIAL_SCHEMA = "torque.denial/1"
# A delegated denial's reason class (task D9): 2 to 48 lowercase letters, digits
# and hyphens, starting with a letter or digit.
REASON_CLASS = re.compile(r"[a-z0-9][a-z0-9-]{1,47}\Z")
# `approval status --wait`'s own exit contract (task D10, spec requirement 21):
# granted, denied, expired and timeout each get a distinct code so an
# unattended agent session can branch on the result without parsing text.
# Anything else (a bad --wait value, an R46 violation, an unreadable denial
# file) is a plain usage or workspace error, exit 2, never one of these four.
WAIT_CODES = {"granted": 0, "denied": 20, "expired": 21, "timeout": 22}
# The most seconds `approval status --wait` may be told to poll for.
WAIT_MAX = 3600
# sf argv[1:3] (or [1:2]) -> the view's normalized operation name.
_SF_OPERATIONS = {("project", "deploy", "start"): "deploy", ("project", "deploy", "quick"): "deploy",
                  ("project", "deploy", "resume"): "deploy", ("project", "delete"): "delete",
                  ("data", "create"): "data create", ("data", "update"): "data update",
                  ("data", "upsert"): "data upsert", ("data", "import"): "data import",
                  ("data", "delete"): "delete", ("apex", "run"): "apex run", ("org", "open"): "org open"}
# Legacy sfdx word -> the view's normalized operation name.
_SFDX_OPERATIONS = {"force:source:deploy": "deploy", "force:mdapi:deploy": "deploy", "force:source:push": "deploy",
                    "force:data:record:create": "data create", "force:data:record:update": "data update",
                    "force:data:record:delete": "delete", "force:apex:execute": "apex run",
                    "force:org:open": "org open"}
_TORQUE_OPERATIONS = {"deploy": "deploy", "recover": "recover", "revert": "revert"}


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
    try:
        missing = _component_files(argv, Path(cwd), [10 ** 9])[1]
    except _TooLarge:
        missing = []
    problems += [f"{component} has no file in this project's package folders" for component in missing]
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


DEPLOY_WORDS = ("deploy", "force:source:deploy", "force:source:push", "force:mdapi:deploy")
SELECTOR_FLAGS = ("-m", "--metadata", "-x", "--manifest", "-d", "--source-dir", "--sourcepath", "-p",
                  "--metadata-dir", "--deploydir")


def _is_deploy(argv: list[str]) -> bool:
    return any(tok in DEPLOY_WORDS for tok in argv[1:4]) and "import" not in argv[1:4]


def _component_files(argv: list[str], cwd: Path, budget: list[int]) -> tuple[list[Path], list[str]]:
    """(project files the command's components come from, named components with no
    local file). A deploy with no selector deploys the whole project (every package
    folder); a wildcard or type-only selector covers every file too."""
    legacy = argv_flags.is_legacy(argv)
    components = _flag_items(argv, COMPONENT_FLAGS)
    manifests = _flag_items(argv, before_state.MANIFEST_FLAGS)
    selectorless = _is_deploy(argv) and not argv_flags.values(argv, SELECTOR_FLAGS, legacy=legacy)
    if not (components or manifests or selectorless):
        return [], []
    files = [cwd / extra for extra in ("sfdx-project.json", ".forceignore") if (cwd / extra).is_file()]
    try:
        named = before_state.deploy_components(argv, cwd) if (components or manifests) else []
    except ws.WorkspaceError:
        named = components
    everything = selectorless
    wanted: dict[str, list[str]] = {}
    for component in named:
        kind, _, name = component.partition(":")
        if not name or "*" in name:
            everything = True
            continue
        if kind == "File":
            continue
        wanted[component] = before_state.payload_needles(component)
    matched: set[str] = set()
    for folder in _package_dirs(cwd):
        for path in _walk(folder, budget):
            text = "/" + path.as_posix()
            hit = [c for c, needles in wanted.items() if any(n in text for n in needles)]
            matched.update(hit)
            if everything or hit:
                files.append(path)
    return files, [c for c in wanted if c not in matched]


def payload_files(argv: list[str], cwd: Path, capped: bool = True) -> list[Path] | None:
    """The local files that decide what the command writes: named files and folders,
    a tree-import plan's data files, the manifest, the project files each named
    component comes from (shared files such as CustomLabels included), and the whole
    project for a deploy with no selector. None when over the caps (capped only)."""
    cwd = Path(cwd)
    budget = [PAYLOAD_WALK_CAP if capped else 10 ** 9]
    files: list[Path] = []
    try:
        for path in named_payload(argv, cwd):
            files += _walk(path, budget)
        files += _component_files(argv, cwd, budget)[0]
    except _TooLarge:
        return None
    unique = list(dict.fromkeys(files))
    return None if capped and len(unique) > PAYLOAD_FILE_CAP else unique


def _payload_entries(argv: list[str], cwd: Path, capped: bool = True) -> list[tuple[Path, str]] | None:
    """(path, content sha256) for every payload file, read exactly the way the
    digest needs it: a symlink is hashed as its target text (`link:<target>\\0`)
    then the content read through it, an unreadable file as `<unreadable>`. None
    when the payload is over the caps (capped only). The single source both
    payload_digest and payload_listing read, so a view's file list and its
    rolled-up digest can never disagree."""
    cwd = Path(cwd)
    files = payload_files(argv, cwd, capped=capped)
    if files is None:
        return None
    total = 0
    out: list[tuple[Path, str]] = []
    for path in files:
        try:
            data = path.read_bytes()
            if path.is_symlink():
                data = ("link:" + os.readlink(path) + "\0").encode() + data
        except OSError:
            data = b"<unreadable>"
        total += len(data)
        if capped and total > PAYLOAD_BYTES_CAP:
            return None
        out.append((path, hashlib.sha256(data).hexdigest()))
    return out


def _payload_name(path: Path, cwd: Path) -> str:
    try:
        return path.relative_to(cwd).as_posix()
    except ValueError:
        return path.as_posix()


def payload_digest(argv: list[str], cwd: Path, capped: bool = True) -> tuple[str | None, int]:
    """(sha256 over each payload file's path relative to cwd and content hash, file
    count), or (None, 0) over the caps. A link is hashed as its target text and the
    content read through it."""
    cwd = Path(cwd)
    entries = _payload_entries(argv, cwd, capped=capped)
    if entries is None:
        return None, 0
    lines = [f"{_payload_name(path, cwd)}\0{sha}\n" for path, sha in entries]
    return "sha256:" + hashlib.sha256("".join(sorted(lines)).encode()).hexdigest(), len(entries)


def _dirs(workspace, client, create: bool = True) -> dict[str, Path]:
    folder, _, _ = ws.load_client(workspace, client)
    base = ws._inside(folder, folder / "approvals")
    out = {"client": folder, "base": base}
    for name in ("requests", "granted", "consumed"):
        out[name] = ws._inside(folder, base / name)
        if create:
            out[name].mkdir(mode=0o700, parents=True, exist_ok=True)
    out["denied"] = ws._inside(folder, base / "denied")
    return out


def _resolver(resolve):
    if resolve is not None:
        return resolve
    from jsc_revert.org_detect import resolve_org
    return resolve_org


def _usable_consent(workspace, client) -> dict:
    item = consent.load_consent(workspace, client)
    problems = consent.consent_problems(item, client=ws.slug_for(client))
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
            "payload_check": "gate", "cwd": str(cwd),
            "namespaces": find_namespaces([text], Path(".")), "components": [],
            "mcp": {"tool_name": tool_name, "tool_input": tool_input}}


def _derive(req: dict, extra_namespaces=()) -> dict:
    if req.get("kind") in ("command", "mcp"):
        cwd = Path(str(req.get("cwd") or ""))
        if not cwd.is_absolute() or not cwd.is_dir():
            raise ws.WorkspaceError("the request's working folder is missing")
        if req["kind"] == "command":
            return _derive_command(req.get("argv") or req.get("payload_argv"), req.get("org_alias"), cwd,
                                   extra_namespaces)
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


def recovery_snapshot_id(command: str) -> str | None:
    """The snapshot a recovery command restores, read with the recovery's own argument
    parser after Torque's --workspace/--client are taken out (so options may come
    anywhere). None for a command that is not a recovery. Raises for a recovery
    command the parser does not accept, so it can never pass unbound."""
    words = command_words(command)
    if not words:
        return None
    head, rest = words
    if head == "torque":
        from .cli import _context_options
        try:
            rest, _, _ = _context_options(list(rest))
        except ws.WorkspaceError as exc:
            raise ws.WorkspaceError(f"this recovery command cannot be read: {exc}") from exc
        if rest[:1] == ["recover"]:
            tail = rest[1:]
            if tail[:1] == ["run"]:
                tail = ["exec", *tail[1:]]
            jsc_args = ["revert", *tail]
        elif rest[:1] == ["revert"]:
            jsc_args = rest[1:]
        else:
            return None
    else:
        jsc_args = list(rest)
    if jsc_args[:1] != ["revert"]:
        return None
    from jsc_revert.cli import build_parser
    import contextlib
    import io as _io
    try:
        with contextlib.redirect_stderr(_io.StringIO()), contextlib.redirect_stdout(_io.StringIO()):
            args = build_parser().parse_args(jsc_args)
    except SystemExit:
        raise ws.WorkspaceError("this recovery command cannot be read by the recovery's own parser") from None
    if getattr(args, "revert_action", None) != "exec":
        return None
    return args.snapshot_id


def _recovery_snapshot(workspace, client, snapshot_id: str, org_alias: str, org_id: str) -> tuple[Path, list[str]]:
    """(the snapshot folder, the command the recovery will run), read the way the
    recovery executor reads them, for the client's own snapshot store."""
    folder, _, _ = ws.load_client(workspace, client)
    previous = os.environ.get("TORQUE_WORKSPACE")
    os.environ["TORQUE_WORKSPACE"] = str(folder)
    try:
        from jsc_revert import manifest as mf, revert_planner
        try:
            snap_dir, snap = mf.load_by_id(org_id[:15], org_alias, snapshot_id)
        except (FileNotFoundError, ValueError, OSError) as exc:
            raise ws.WorkspaceError(f"cannot find recovery snapshot {snapshot_id} for {org_alias}: {exc}") from exc
        if str(snap.get("org", {}).get("org_id_18", ""))[:15] != org_id[:15]:
            raise ws.WorkspaceError("the recovery snapshot belongs to another org")
        plan = recovery_operation(revert_planner.build_revert_command(
            {**snap, "org": {**snap["org"], "alias": org_alias}}, snap_dir))
    finally:
        if previous is None:
            os.environ.pop("TORQUE_WORKSPACE", None)
        else:
            os.environ["TORQUE_WORKSPACE"] = previous
    return Path(snap_dir), list(plan or [])


def _bind_recovery(derived: dict, workspace, client, org_alias: str, org_id: str) -> dict:
    """For a recovery command, bind every file of its snapshot (manifest and captured
    before-state) and show the command the recovery will run. A change to the
    snapshot after the grant then refuses the approval."""
    snapshot_id = recovery_snapshot_id(derived["command"]) if derived.get("payload_argv") else None
    if snapshot_id is None:
        return {**derived, "recovery_snapshot": None, "recovery_snapshot_dir": None, "recovery_plan": None}
    snap_dir, plan = _recovery_snapshot(workspace, client, snapshot_id, org_alias, org_id)
    payload_argv = [*derived["payload_argv"], "--recovery-snapshot", str(snap_dir)]
    cwd = Path(str(derived["cwd"]))
    problems = payload_problems(["recover", "--source-dir", str(snap_dir)], cwd)
    if problems:
        raise ws.WorkspaceError("the recovery snapshot cannot be bound: " + "; ".join(problems[:5]))
    digest, count = payload_digest(payload_argv, cwd, capped=False)
    return {**derived, "payload_argv": payload_argv, "payload_digest": digest, "payload_files": count,
            "payload_check": "gate" if payload_digest(payload_argv, cwd)[0] else "wrapper",
            "recovery_snapshot": snapshot_id, "recovery_snapshot_dir": os.path.realpath(str(snap_dir)),
            "recovery_plan": plan}


def recovery_operation(command) -> list[str]:
    """A recovery command without the way this interpreter starts the wrapper (`jsc` or
    `python -m jsc_revert.cli`), so a grant and a run from different installs agree."""
    if not command:
        return []
    from jsc_revert import revert_planner
    prefix = revert_planner._jsc_command()
    command = list(command)
    if command[:len(prefix)] == prefix:
        return command[len(prefix):]
    for i, word in enumerate(command[:3]):
        if word == "jsc_revert.cli" or word.endswith(("/jsc", "\\jsc", "jsc.exe")) or word == "jsc":
            return command[i + 1:]
    return command


def recovery_problem(approved: dict, snapshot_dir, plan: list[str]) -> str:
    """Why a recovery run may not use this approval: it must restore the approved
    snapshot, from the approved folder, with exactly the approved operation."""
    if not approved.get("recovery_snapshot") or not approved.get("recovery_snapshot_dir"):
        return "this approval does not name a recovery snapshot"
    if os.path.realpath(str(snapshot_dir)) != approved["recovery_snapshot_dir"]:
        return (f"the recovery would load snapshot files from {snapshot_dir}, not the approved "
                f"{approved['recovery_snapshot_dir']}")
    if recovery_operation(plan) != list(approved.get("recovery_plan") or []):
        return "the recovery operation differs from the approved one"
    return ""


def _extra_namespaces(workspace, config: dict | None = None) -> tuple[str, ...]:
    """Namespaces workspace.json adds to the managed-package list. The delegated
    grant passes the config its caller proof already read (one protected read)."""
    if config is None:
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
        req = {"kind": kind, "argv": list(argv), "payload_argv": list(argv), "cwd": str(cwd), "org_alias": org_alias}
    derived = _derive(req, _extra_namespaces(workspace))
    org_id, org_kind = _org_identity(item, org_alias, resolve)
    derived = _bind_recovery(derived, workspace, client, org_alias, org_id)
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


def load_request_hashed(workspace, client, request_id) -> tuple[dict, str]:
    """The request and the SHA-256 of the exact bytes it was read from. Every field
    a view or a grant relies on is validated here, so a tampered or malformed
    request surfaces as a WorkspaceError once, rather than a raw KeyError or
    ValueError later."""
    if not _valid_id(request_id, "req-"):
        raise ws.WorkspaceError("a request ID looks like req-0123456789ab")
    path = _dirs(workspace, client, create=False)["requests"] / f"{request_id}.json"
    try:
        raw = path.read_bytes()
        req = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise ws.WorkspaceError(f"no readable request {request_id} for this client") from exc
    if not isinstance(req, dict) or req.get("schema") != REQUEST_SCHEMA or req.get("id") != request_id \
            or req.get("client") != ws.slug_for(client):
        raise ws.WorkspaceError(f"invalid request file: {path}")
    if req.get("kind") not in KINDS or not isinstance(req.get("org_alias"), str) or not req.get("org_alias"):
        raise ws.WorkspaceError(f"invalid request file: {path}")
    try:
        _epoch(req.get("created_at"))
    except (TypeError, ValueError) as exc:
        raise ws.WorkspaceError(f"invalid request file: {path}") from exc
    return req, "sha256:" + hashlib.sha256(raw).hexdigest()


def request_sha256_of(workspace, client, request_id) -> str:
    return load_request_hashed(workspace, client, request_id)[1]


def operation_for(kind: str, argv: list[str] | None, mcp: dict | None) -> str:
    """The view's normalized operation name for a request: one of `deploy`,
    `data create/update/upsert/import`, `delete`, `apex run`, `recover`, `revert`,
    `org open`, `browser window`, `mcp:<tool>` or `other:<words>`."""
    if kind == "browser":
        return "browser window"
    if kind == "mcp":
        return "mcp:" + str((mcp or {}).get("tool_name"))
    words = [w for w in (argv or [])[1:] if not w.startswith("-")]
    head = os.path.basename((argv or [""])[0])
    if head in ("torque", "jsc") or (argv and command_words(shlex.join(argv))):
        found = command_words(shlex.join(argv))
        rest = [w for w in (found[1] if found else words) if not w.startswith("-")]
        if rest and rest[0] in _TORQUE_OPERATIONS:
            return _TORQUE_OPERATIONS[rest[0]]
        if rest[:1] == ["data"] and len(rest) > 1:
            return "delete" if rest[1] == "delete" else f"data {rest[1]}"
        return "other:" + " ".join(rest[:3])
    if head == "sfdx" and words:
        return _SFDX_OPERATIONS.get(words[0], "other:" + words[0])
    for size in (3, 2):
        if tuple(words[:size]) in _SF_OPERATIONS:
            return _SF_OPERATIONS[tuple(words[:size])]
    return "other:" + " ".join(words[:3])


def view_components(argv: list[str] | None, cwd) -> list[str]:
    """What a command changes, normalized (sorted, unique) for the view only: deploy
    components, Record:Object:Id, Record:Object:external:Field for an upsert (both
    flag spellings), or (when the write names no component Torque can list)
    Record:Object:where:CLAUSE. R44: an upsert is normalized here, never inside
    before_state.write_components, which grant()'s production before-state check
    (via _derive_command) still reads at its unchanged a15 behavior; folding this
    into that function let a long-flag upsert grant in production with no
    before-state, via --new-component, which a15 always refused outright."""
    if not argv:
        return []
    words = [w for w in argv[1:] if not w.startswith("-")][:3]
    if tuple(words) == ("data", "upsert", "record"):
        sobject = argv_flags.values(argv, ("-s", "--sobject"))
        field = argv_flags.values(argv, ("-i", "--external-id"))
        found = [f"Record:{sobject[0]}:external:{field[0]}"] if sobject and field else []
    else:
        found = before_state.write_components(list(argv), Path(cwd))
    if not found:
        sobject = argv_flags.values(argv, ("-s", "--sobject"))
        where = argv_flags.values(argv, ("-w", "--where"))
        if sobject and where:
            found = [f"Record:{sobject[0]}:where:{' '.join(where[0].split())}"]
    return sorted(set(found))


def payload_listing(argv: list[str] | None, cwd) -> list[dict]:
    """Every payload file, path relative to cwd and its own sha256 (the same hash
    payload_digest folds into its rolled-up digest), sorted by path."""
    if not argv:
        return []
    cwd = Path(cwd)
    entries = _payload_entries(list(argv), cwd, capped=False) or []
    return sorted(({"path": _payload_name(path, cwd), "sha256": sha} for path, sha in entries),
                  key=lambda f: f["path"])


def _screen_lines(req: dict, derived: dict, org_id: str, org_kind: str, kind_line: str,
                  *, middle: tuple[str, ...] = ()) -> list[str]:
    """The lines every approval screen shares: client, org, kind, call, working
    folder and components, then (with room in `middle` for the grant screen's own
    New/Check-only/Before rows) namespaces and payload. Raw (unescaped): grant()
    escapes each line itself at write time; screen_lines() escapes the whole list
    once for the view."""
    return [f"Client:      {req['client']}    Change: {req.get('change')}",
            f"Org:         {req['org_alias']}  {org_id}  {org_kind.upper()}",
            kind_line,
            f"Call:        {derived['command']}",
            f"Working in:  {derived['cwd'] or 'n/a'}",
            f"Components:  {', '.join(derived['components']) or 'not listed'}",
            *middle,
            f"Namespaces:  {', '.join(derived['namespaces']) or 'none'}",
            f"Payload:     {derived['payload_digest'] or 'n/a'} ({derived['payload_files']} files)"]


def _ttl(req: dict) -> int:
    """The approval window this request's kind (and, for a browser window, its
    requested minutes, capped) allows. Shared by request_view and grant so a
    request's advertised expiry and its actual grant window can never drift
    apart."""
    kind = req["kind"]
    minutes = req.get("browser_minutes") if kind == "browser" else None
    return min(TTL_SECONDS[kind], int(minutes) * 60) if minutes else TTL_SECONDS[kind]


def screen_lines(req: dict, derived: dict, org_id: str, org_kind: str, ttl: int) -> list[str]:
    """What a delegated approver reviews: the grant screen without the terminal
    prompt, every line escaped through printable() exactly once (control and
    bidirectional-override characters shown as escapes, never printed raw)."""
    kind_line = f"Kind:        {req['kind']}; valid {ttl // 60} minutes"
    lines = [*_screen_lines(req, derived, org_id, org_kind, kind_line),
             f"Purpose:     {req.get('purpose') or 'n/a'}"]
    return [printable(line) for line in lines]


def request_view(workspace, client, request_id, *, resolve=None) -> dict:
    """The parsed, normalized request an automated approver matches and reviews.
    Everything is derived again from the request's command; the org is resolved
    live and must still match the consent."""
    req, sha = load_request_hashed(workspace, client, request_id)
    item = _usable_consent(workspace, client)
    derived = _derive(req, _extra_namespaces(workspace))
    org_id, org_kind = _org_identity(item, req["org_alias"], resolve)
    kind = req["kind"]
    minutes = req.get("browser_minutes") if kind == "browser" else None
    ttl = _ttl(req)
    argv = derived.get("payload_argv") if kind == "command" else None
    cwd = derived.get("cwd")
    return {"schema": VIEW_SCHEMA, "request_id": request_id, "request_sha256": sha, "client": req["client"],
            "change": req.get("change"), "kind": kind, "operation": operation_for(kind, argv, req.get("mcp")),
            "org_alias": req["org_alias"], "org_id_18": org_id, "org_kind": org_kind,
            "components": view_components(argv, cwd) if argv else [], "cwd": cwd,
            "payload": {"digest": derived["payload_digest"], "count": derived["payload_files"],
                        "files": payload_listing(derived.get("payload_argv"), cwd) if cwd else []},
            "purpose": req.get("purpose"), "browser_minutes": minutes,
            "before_state_event": req.get("before_state_event"), "manual_recovery": req.get("manual_recovery"),
            "validated_job": req.get("validated_job"), "created_at": req.get("created_at"),
            "expires_at": _iso(_epoch(req["created_at"]) + REQUEST_TTL) if req.get("created_at") else None,
            "screen": screen_lines(req, derived, org_id, org_kind, ttl)}


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


def _check_review(current_sha: str, derived: dict, request_sha256, payload_digest) -> None:
    """The grant covers exactly what was reviewed: the request file's bytes and the
    digest over the payload file set and contents. Either differing refuses. Either
    argument left None (the a15 owner call, before this task) skips its check."""
    if request_sha256 is not None and request_sha256 != current_sha:
        raise delegation.Refusal("request-changed", "the request file changed after it was reviewed; nothing "
                                                    "was granted")
    if payload_digest is not None and payload_digest != (derived["payload_digest"] or "none"):
        raise delegation.Refusal("payload-changed", "the files this call uses changed after they were reviewed; "
                                                    "nothing was granted")


def grant(workspace, client, request_id, *, new_components=(), presence=None, confirm=None, out=None,
          resolve=None, now=None, report=None, audit_trail=None, delegated=False, model_id=None,
          request_sha256=None, payload_digest=None, idempotency_key=None, env=None, ancestors=None,
          getuid=None, root_owner=None, control_stat=None) -> dict:
    """The consultant's grant, at a real terminal, after reading the call. With
    `delegated`, the workspace's delegated approver's grant instead (tier 2, no
    terminal; see _grant_delegated). `control_stat`, like `root_owner`, is an
    injectable override for a caller that cannot make the workspace's control
    files and folders genuinely belong to another account (fix round 1: a
    contract or test that needs this fakes ownership for this one call through
    the parameter, never by swapping the process-global `_control_stat`, which
    would also change what a concurrent caller in the same process sees)."""
    if delegated:
        if new_components:
            raise ws.WorkspaceError("--new-component belongs to the consultant's production grants; a delegated "
                                    "grant never covers production")
        return _grant_delegated(workspace, client, request_id, model_id=model_id, request_sha256=request_sha256,
                                payload_digest=payload_digest, idempotency_key=idempotency_key, out=out,
                                resolve=resolve, now=now, env=env, ancestors=ancestors, getuid=getuid,
                                root_owner=root_owner, control_stat=control_stat)
    if model_id is not None:
        raise ws.WorkspaceError("--model-id applies only to a delegated grant (--delegated)")
    if idempotency_key is not None:
        raise ws.WorkspaceError("--idempotency-key applies only to a delegated grant (--delegated)")
    _require_operator(presence)
    out = out or sys.stdout
    req, current = load_request_hashed(workspace, client, request_id)
    change_id = req.get("change")
    changes.load_change(workspace, client, change_id)
    if _denied(workspace, client, change_id, request_id) \
            or _delegated_denial(workspace, client, request_id, control_stat=control_stat):
        raise ws.WorkspaceError(f"{request_id} was denied; ask for a new request")
    config = ws.load_workspace(workspace)[1]
    if _ai_approver(config):
        raise delegation.Refusal("human-grant-needs-human-approver", HUMAN_GRANT_REFUSAL)
    item = _usable_consent(workspace, client)
    derived = _derive(req, _extra_namespaces(workspace))
    _check_review(current, derived, request_sha256, payload_digest)
    org_id, org_kind = _org_identity(item, req["org_alias"], resolve)
    derived = _bind_recovery(derived, workspace, client, req["org_alias"], org_id)
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
    ttl = _ttl(req)
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
    kind_line = (f"Kind:        {kind}; valid {ttl // 60} minutes"
                + ("; single use" if kind != "browser" else "; every browser action in the window"))
    middle = [f"New:         {', '.join(new_components) or 'none declared'}",
              f"Check-only:  {job_line}",
              "Before:      " + (f"{before['path']} ({len(before['files'])} files, {before.get('how') or 'recorded'}, "
                                 f"captured {before['captured_at']}"
                                 + (f" from {before['org_id_18']})" if before.get("org_id_18")
                                    else "; org not verified)")
                                 if before else recovery or ("not required" if org_kind != "production" else "n/a"))]
    lines = [
        *_screen_lines(req, derived, org_id, org_kind, kind_line, middle=middle),
        *([f"Recovery:    snapshot {derived['recovery_snapshot']} will run: "
           f"{shlex.join(derived['recovery_plan']) if derived['recovery_plan'] else 'NOTHING (no plan)'}"]
          if derived.get("recovery_snapshot") else []),
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
    record = _base_record(
        req, request_id, derived, org_id, org_kind, t=t, ttl=ttl,
        before_state=({"event_id": before["event_id"], "sha256": before["sha256"], "path": before["path"],
                       "captured_at": before["captured_at"], "how": before.get("how"),
                       "org_id_18": before.get("org_id_18"), "job": before.get("job")} if before else None),
        manual_recovery=recovery, new_components=new_components,
        approver={"approver": _user(), "approver_uid": os.getuid() if hasattr(os, "getuid") else None,
                  "approver_kind": "human", "approver_model": None, "delegated": False})
    verify = config.get("approval_verify", "hmac")
    if verify == "owner-uid":
        if not hasattr(os, "getuid") or os.getuid() != config.get("approver_uid"):
            raise ws.WorkspaceError("this workspace takes approvals only from the approver account "
                                    f"(uid {config.get('approver_uid')}); grant from that account")
    else:
        record["signature"] = _sign(record, _key(create=True))
    dirs = _dirs(workspace, client)
    if delegation.delegated_tier2(config):
        # Fix round 1, item 3: only a workspace with a named tier 2 approver
        # delegate can ever race against deny_delegated (it cannot run
        # anywhere else), so the marker is skipped entirely for every other
        # workspace; a plain a15 grant writes nothing new here.
        decision = _claim_decision(dirs, request_id, "granted", record["id"])
        if decision["outcome"] != "granted":
            raise ws.WorkspaceError(f"{request_id} was denied; ask for a new request")
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


def _base_record(req: dict, request_id: str, derived: dict, org_id: str, org_kind: str, *, t: float, ttl: int,
                 approver: dict, before_state=None, manual_recovery=None, new_components=()) -> dict:
    """The approval record both grant paths write (F28), in the a15 field order. The
    identity block `approver` names approver, approver_uid, approver_kind,
    approver_model and delegated."""
    kind = req["kind"]
    return {"schema": SCHEMA, "id": "apr-" + secrets.token_hex(6), "request_id": request_id,
            "client": req["client"], "change": req.get("change"), "kind": kind, "command": derived["command"],
            "call_key": derived["call_key"], "command_sha256": derived["command_sha256"],
            "payload_digest": derived["payload_digest"], "payload_check": derived["payload_check"],
            "payload_argv": derived["payload_argv"], "cwd": derived["cwd"], "org_alias": req["org_alias"],
            "org_id_18": org_id, "org_kind": org_kind, "before_state": before_state,
            "manual_recovery": manual_recovery, "validated_job": req.get("validated_job"),
            "new_components": list(new_components), "namespaces": derived["namespaces"],
            "recovery_snapshot": derived.get("recovery_snapshot"), "recovery_plan": derived.get("recovery_plan"),
            "recovery_snapshot_dir": derived.get("recovery_snapshot_dir"),
            **{k: approver[k] for k in ("approver", "approver_uid", "approver_kind", "approver_model", "delegated")},
            "granted_at": _iso(t), "expires_at": _iso(t + ttl), "single_use": kind != "browser"}


def _grant_delegated(workspace, client, request_id, *, model_id, request_sha256, payload_digest, idempotency_key,
                     out, resolve, now, env, ancestors, getuid, root_owner, control_stat) -> dict:
    """A delegated approver's grant: tier 2, no terminal, never for production or an
    unknown org, bound to the request it reviewed, recorded with its kind and
    identity. Every decision comes from the one protected read of workspace.json
    the caller proof made (R46: that file and the client's consent.json must not be
    owned or writable by the approver account). Writes only the approval file (the
    change record is the agent's; the gate logs the grant when the approval is used).
    Every refusal is a delegation.Refusal with a reason class: an unreadable,
    future-dated or underivable request or change record is `request-changed`; a
    request past the request TTL is `request-expired`. With `idempotency_key`
    (D7), a repeat of the exact same request, reviewed hash and payload digest
    returns the earlier grant instead of making a new one; the agent-session,
    tier 2, R46 control-file/folder and reviewed-hash checks above still run on
    every call, so an idempotency key never bypasses them. `control_stat`
    (fix round 1) is `_controls_problem`'s own injectable owner lookup, threaded
    through rather than defaulted here, so a caller with no way to actually chown
    the workspace's control files can prove this exact call's R46 check without
    touching the module-global `_control_stat` that other, unrelated calls in the
    same process also read."""
    actor, config, root, config_st = delegation._delegated_proof(
        workspace, "approver", model_id=model_id, getuid=getuid, env=env, ancestors=ancestors,
        root_owner=root_owner)
    if not delegation.delegated_tier2(config):
        raise delegation.Refusal("tier-2-required", "a delegated grant needs tier 2 (owner-uid) approvals and "
                                                    "the workspace's approver account as its named approver")
    try:
        client_folder = ws.load_client(root, client)[0]
    except (OSError, ws.WorkspaceError) as exc:
        raise delegation.Refusal("request-changed", f"cannot read the client folder: {exc}") from None
    controls = _controls_problem(root, client_folder, config["approver_uid"], workspace_st=config_st,
                                 control_stat=control_stat)
    if controls:
        raise delegation.Refusal("not-delegated", controls)
    # The client folder is already known good (the R46 check just read it), so this
    # cannot fail where the earlier try/except above needed to catch it.
    dirs = _dirs(workspace, client, create=False)
    if not isinstance(request_sha256, str) or not request_sha256 \
            or not isinstance(payload_digest, str) or not payload_digest:
        raise delegation.Refusal("request-changed", "a delegated grant names the reviewed request's SHA-256 and "
                                                    "payload digest (--request-sha256, --payload-digest)")
    if idempotency_key is not None:
        # The R46 controls check above already ran, so a lookup here is trusted the
        # same as a fresh grant. Only an exact repeat (same request, same reviewed
        # hash and payload) returns the earlier grant; anything else about this key
        # is a conflict, never a silent reuse of someone else's review.
        existing = find_by_idempotency_key(workspace, client, idempotency_key, config=config,
                                           control_stat=control_stat)
        if existing is not None:
            if existing.get("request_id") != request_id or existing.get("reviewed_request_sha256") != request_sha256 \
                    or (existing.get("payload_digest") or "none") != payload_digest:
                raise delegation.Refusal("idempotency-conflict", "this idempotency key was already used for a "
                                                                  "different request or reviewed content; "
                                                                  "nothing was granted")
            return existing
    out = out or sys.stdout
    t = now if now is not None else time.time()
    try:
        req, current = load_request_hashed(workspace, client, request_id)
        created = _epoch(req["created_at"])
        change_id = req.get("change")
        changes.load_change(workspace, client, change_id)
    except (OSError, ValueError, TypeError, KeyError, ws.WorkspaceError) as exc:
        raise delegation.Refusal("request-changed", f"the request or its change record cannot be read: {exc}") \
            from None
    # Fix round 1, item 2: _delegated_denial's own R46/unreadable-file check must
    # raise with its own reason class (not be swallowed into "request-changed" by
    # the broad except above), so it runs after that try/except, not inside it.
    denied = _denied(workspace, client, change_id, request_id) \
        or _delegated_denial(workspace, client, request_id, control_stat=control_stat)
    if created > t + SKEW:
        raise delegation.Refusal("request-changed", f"{request_id} is dated in the future; nothing was granted")
    if t > created + REQUEST_TTL + SKEW:
        raise delegation.Refusal("request-expired", f"{request_id} is older than {REQUEST_TTL // 60} minutes; "
                                                    "ask for a new request")
    if denied:
        raise delegation.Refusal("request-denied", f"{request_id} was denied; ask for a new request")
    try:
        item = _usable_consent(workspace, client)
    except ws.WorkspaceError as exc:
        raise delegation.Refusal("consent-unusable", str(exc)) from None
    try:
        derived = _derive(req, _extra_namespaces(workspace, config))
    except (OSError, ValueError, TypeError, KeyError, ws.WorkspaceError) as exc:
        raise delegation.Refusal("request-changed", f"the request's call cannot be derived: {exc}") from None
    _check_review(current, derived, request_sha256, payload_digest)
    entry = consent.approved_org(item, req["org_alias"]) or {}
    try:
        org_id, org_kind = _org_identity(item, req["org_alias"], resolve)
    except (ws.WorkspaceError, OSError, ValueError) as exc:
        raise delegation.Refusal("org-production-or-unknown", "delegated approvals need an org identified live "
                                                              f"as non-production: {exc}") from None
    if org_kind not in NONPRODUCTION or entry.get("kind") not in NONPRODUCTION \
            or req.get("org_kind") not in NONPRODUCTION:
        raise delegation.Refusal("org-production-or-unknown", "delegated approvals are refused for production "
                                                              "and unknown orgs; the consultant grants those")
    ttl = _ttl(req)
    for line in screen_lines(req, derived, org_id, org_kind, ttl):
        out.write(line + "\n")
    out.flush()
    record = _base_record(req, request_id, derived, org_id, org_kind, t=t, ttl=ttl,
                          approver={"approver": actor.account, "approver_uid": actor.uid,
                                    "approver_kind": actor.kind, "approver_model": actor.model,
                                    "delegated": True})
    if idempotency_key is not None:
        # Two callers racing for the same key agree on one approval_id here (the
        # atomic step); a key reserved earlier for a different request is the same
        # idempotency-conflict as finding a completed grant for one above.
        reserved = _reserve_key(dirs, idempotency_key, record["id"], request_id)
        if reserved["request_id"] != request_id:
            raise delegation.Refusal("idempotency-conflict", "this idempotency key was already used for a "
                                                              "different request or reviewed content; "
                                                              "nothing was granted")
        record["id"] = reserved["approval_id"]
    # Fix round 1, item 3: the atomic, race-closing check, right before publish.
    # The scan-based `denied` check above already refused a request a denial had
    # already reached disk for; this instead closes the window where a
    # deny_delegated() call racing this one has not written its denied/ file yet
    # but wins the shared decision-<request_id> marker first.
    decision = _claim_decision(dirs, request_id, "granted", record["id"])
    if decision["outcome"] != "granted":
        raise delegation.Refusal("request-denied", f"{request_id} was denied; ask for a new request")
    record["reviewed_request_sha256"] = request_sha256
    record["idempotency_key"] = idempotency_key
    return _publish_grant(workspace, client, record)


def _publish_grant(workspace, client, record: dict) -> dict:
    """Write a delegated grant's approval file, readable by the agent account's gate
    (0644, owned by the approver account that runs this). Two grants that raced to
    the same reserved id (D7's idempotent retry) both try to publish it; the loser's
    write fails because the winner's file already exists, and it reads that file
    back instead of raising, returning it when it is the same idempotent grant."""
    path = _dirs(workspace, client, create=False)["granted"] / f"{record['id']}.json"
    try:
        ws._write_json(path, record)
    except ws.WorkspaceError:
        existing = _read(path)
        if existing is not None and record.get("idempotency_key") is not None \
                and existing.get("idempotency_key") == record["idempotency_key"]:
            return existing
        raise
    path.chmod(0o644)
    return record


def _owner_mismatch(st, approver) -> bool:
    """The one stat-based ownership test (F28): not the approver account's, or
    writable by someone else. Shared, so it is written once, by `_approver_owned`
    and by `_problem`'s owner-uid branch (which still emits its own a15 message per
    case; fix round 1 finding 1)."""
    return st.st_uid != approver or st.st_mode & 0o022


def _approver_owned(path: Path, config: dict) -> str:
    """"" when the file and its folder belong to the tier 2 approver account and no
    one else can write them; otherwise why not. Shared by the gate's owner-uid
    ownership check (F28) and D7's idempotency-marker/record lookup."""
    approver = config.get("approver_uid")
    if config.get("approval_verify") != "owner-uid" or type(approver) is not int:
        return "not a tier 2 workspace"
    try:
        st, folder = path.lstat(), path.parent.stat()
    except OSError:
        return "missing"
    if path.is_symlink() or _owner_mismatch(st, approver):
        return "not owned by the approver account, or writable by others"
    if _owner_mismatch(folder, approver):
        return "its folder is not the approver account's"
    return ""


def _key_marker(dirs: dict, key: str) -> Path:
    """The reservation file for an idempotency key: a name derived from the key
    itself (never the key in the clear in a file name), under approvals/granted so
    it shares that folder's tier 2 ownership."""
    return dirs["granted"] / ("idem-" + hashlib.sha256(key.encode()).hexdigest()[:32] + ".json")


def _reserve_key(dirs: dict, key: str, approval_id: str, request_id: str) -> dict:
    """Claim `key` for `approval_id`/`request_id`, or return whoever claimed it
    first. The claim is the atomic step (O_EXCL): two callers racing for the same
    key agree on one approval_id without a lock."""
    marker = _key_marker(dirs, key)
    value = {"key": key, "approval_id": approval_id, "request_id": request_id}
    if _exclusive(marker, value):
        marker.chmod(0o644)
        return value
    found = _read(marker)
    if not found or found.get("key") != key or not _valid_id(found.get("approval_id"), "apr-"):
        raise ws.WorkspaceError(f"the idempotency marker for {key} is unreadable; nothing was granted")
    return found


DECISION_OUTCOMES = ("granted", "denied")


def _decision_marker(dirs: dict, request_id: str) -> Path:
    """The per-request exclusive decision marker (fix round 1, item 3): a name
    derived from the request_id itself, under approvals/granted so it shares that
    folder's tier 2 ownership, exactly like the idempotency marker."""
    return dirs["granted"] / f"decision-{request_id}.json"


def _claim_decision(dirs: dict, request_id: str, outcome: str, record_id) -> dict:
    """Claim `request_id`'s grant/deny decision, or return whoever claimed it
    first. The claim is the atomic step (O_EXCL, the same primitive
    `_reserve_key` uses): whichever of `grant()`, `_grant_delegated()` or
    `deny_delegated()` reaches this first for a given request owns the outcome.
    The caller compares its own `outcome` against the returned one: a match
    (including a repeat by the same side, such as an idempotent retry or a
    second denial) proceeds normally; a mismatch means the other side won and
    the caller refuses."""
    marker = _decision_marker(dirs, request_id)
    value = {"request_id": request_id, "outcome": outcome, "record_id": record_id}
    if _exclusive(marker, value):
        marker.chmod(0o644)
        return value
    found = _read(marker)
    if not found or found.get("request_id") != request_id or found.get("outcome") not in DECISION_OUTCOMES:
        raise ws.WorkspaceError(f"the grant/deny decision marker for {request_id} is unreadable; nothing "
                                "was decided")
    return found


def find_by_idempotency_key(workspace, client, key, *, config=None, control_stat=None) -> dict | None:
    """The approval already granted under `key`, or None when no grant has
    completed for it yet (no reservation, a reservation with no published
    approval, or either file untrustworthy under this workspace's tier 2 rule).
    R46 keeps running here too: a lookup in a tier 2 workspace whose control files
    or folders belong to the approver account trusts nothing it finds (D7).
    `control_stat` (fix round 1) is the same injectable owner lookup
    `_grant_delegated` threads through when it calls this for an idempotent
    retry; a standalone lookup (the CLI's `approval lookup`) leaves it at the
    default, `approval._control_stat`."""
    if not isinstance(key, str) or not IDEMPOTENCY_KEY.fullmatch(key):
        raise ws.WorkspaceError("an idempotency key is 8 to 128 letters, digits and : . _ -")
    config = config if config is not None else _config(workspace)
    dirs = _dirs(workspace, client, create=False)
    marker = _key_marker(dirs, key)
    found = _read(marker)
    if not found or _approver_owned(marker, config):
        return None
    approver = config.get("approver_uid")
    if config.get("approval_verify") == "owner-uid" and type(approver) is int:
        try:
            root = ws.load_workspace(workspace)[0]
        except (OSError, ws.WorkspaceError):
            return None
        if _controls_problem(root, dirs["client"], approver, control_stat=control_stat):
            return None
    path = dirs["granted"] / f"{found.get('approval_id')}.json"
    record = _read(path)
    if record is None or _approver_owned(path, config) or record.get("idempotency_key") != key:
        return None
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
                                   "approver": record["approver"], "approver_uid": record.get("approver_uid"),
                                   "approver_kind": record.get("approver_kind"),
                                   "approver_model": record.get("approver_model"),
                                   "delegated": record.get("delegated"), "granted_at": record["granted_at"],
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
                                          "payload_digest": req.get("payload_digest"),
                                          "org_alias": req.get("org_alias"), "org_id_18": req.get("org_id_18"),
                                          "org_kind": req.get("org_kind"), "approver": _user(),
                                          "before_state_event": req.get("before_state_event"),
                                          "manual_recovery": req.get("manual_recovery"),
                                          "validated_job": req.get("validated_job")})


def _authentic_grant(path: Path, record: dict, config: dict, client: str, *, control_stat=None) -> bool:
    """Whether a file under approvals/granted/ is a real grant this workspace's
    tier 2 approver made: the same record-field, ownership and R46 controls checks
    the gate applies before consuming a grant (`_problem`), without the time
    window (F21: an expired-but-genuine grant still counts as "already granted"
    for the exclusivity rule between a grant and a delegated denial; a time-based
    check belongs to whether the grant can still be *used*, not whether it was
    genuinely made). `control_stat`: see `_controls_problem`."""
    if record.get("schema") != SCHEMA or any(k not in record for k in REQUIRED) \
            or record.get("kind") not in KINDS or not _valid_id(record.get("id"), "apr-") \
            or path.name != f"{record.get('id')}.json" or record.get("client") != client:
        return False
    approver = config.get("approver_uid")
    if config.get("approval_verify") != "owner-uid" or type(approver) is not int or path.is_symlink():
        return False
    try:
        st, folder = path.lstat(), path.parent.stat()
    except OSError:
        return False
    if _owner_mismatch(st, approver) or _owner_mismatch(folder, approver):
        return False
    layout = _granted_layout(path, client)
    if layout is None or _controls_problem(*layout, approver, control_stat=control_stat):
        return False
    return not _identity_problem(record, config, "owner-uid")


def _granted_for(workspace, client, request_id, config, *, control_stat=None) -> bool:
    """F21: an authentic grant already exists for this request, checked the same
    way the gate verifies a grant it is about to consume (ownership, R46
    controls, record fields), so a forged or approver-unowned file under
    approvals/granted/ can never itself block a delegated denial. `control_stat`:
    see `_controls_problem`."""
    dirs = _dirs(workspace, client, create=False)
    slug = ws.slug_for(client)
    return any(record.get("request_id") == request_id
              and _authentic_grant(path, record, config, slug, control_stat=control_stat)
              for path, record in _granted(dirs))


def deny_delegated(workspace, client, request_id, reason_class, *, model_id, reason, env=None, ancestors=None,
                   getuid=None, now=None, root_owner=None, control_stat=None) -> dict:
    """A delegated approver's refusal, published where the waiting session reads it
    (D10) and the log lists it (D16). A denial passes the same identity and
    control checks a delegated grant does: agent-session, not-delegated (no
    delegate, wrong uid, R41 separate-account, workspace.json owner/mode), tier 2
    (owner-uid, connected, the approver delegate matches config["approver_uid"]),
    and R46 (the workspace root, clients/, the client folder, workspace.json and
    consent.json must not belong to or be writable by the approver account).
    Writes exactly one file the approver owns in approvals/denied/ (mode 0644)
    and nothing else: no change-record event, no activity.jsonl entry (unlike the
    consultant's own `deny`, which is a change event and writes no file here).

    F21 / exclusivity: refused with reason class `already-granted` when an
    authentic grant already exists for this request (`_granted_for`, a scan of
    approvals/granted/), so a request already granted can never also collect a
    denial. The matching direction (a request already denied, delegated or not,
    refuses a later grant) is enforced in `grant`/`_grant_delegated` via
    `_delegated_denial`. Fix round 1, item 3: right before this function writes
    anything, it also claims the shared `decision-<request_id>` marker
    (`_claim_decision`, the same O_EXCL primitive the idempotency marker uses);
    `grant`/`_grant_delegated` claim the same marker right before they publish a
    grant. Whichever side reaches the marker first for a given request owns the
    outcome; the loser refuses even when the scan above saw nothing yet (the race
    window a check-then-write pattern alone cannot close). A repeat denial (this
    same side winning or re-reading its own earlier claim) always sees
    outcome == "denied" and proceeds normally; a request may be denied more than
    once, and nothing here treats a repeat denial as a conflict.

    `root_owner` and `control_stat` are the same injectable overrides
    `grant`/`_grant_delegated` take, for a caller that cannot make the
    workspace's control files and folders genuinely belong to a separate OS
    account. Fail closed: an unreadable or malformed request, workspace
    configuration or control file refuses rather than being treated as absent."""
    actor, config, root, config_st = delegation._delegated_proof(
        workspace, "approver", model_id=model_id, getuid=getuid, env=env, ancestors=ancestors,
        root_owner=root_owner)
    if not delegation.delegated_tier2(config):
        raise delegation.Refusal("tier-2-required", "a delegated denial needs tier 2 (owner-uid) approvals and "
                                                    "the workspace's approver account as its named approver")
    try:
        client_folder = ws.load_client(root, client)[0]
    except (OSError, ws.WorkspaceError) as exc:
        raise delegation.Refusal("request-changed", f"cannot read the client folder: {exc}") from None
    controls = _controls_problem(root, client_folder, config["approver_uid"], workspace_st=config_st,
                                 control_stat=control_stat)
    if controls:
        raise delegation.Refusal("not-delegated", controls)
    if not isinstance(reason_class, str) or not REASON_CLASS.fullmatch(reason_class):
        raise ws.WorkspaceError("a reason class is 2 to 48 lowercase letters, digits and hyphens")
    if not isinstance(reason, str) or not reason.strip():
        raise ws.WorkspaceError("give a reason")
    try:
        req, sha = load_request_hashed(workspace, client, request_id)
    except ws.WorkspaceError as exc:
        raise delegation.Refusal("request-changed", str(exc)) from None
    if _granted_for(workspace, client, request_id, config, control_stat=control_stat):
        raise delegation.Refusal("already-granted", f"{request_id} already has a granted approval; nothing "
                                                    "was denied")
    dirs = _dirs(workspace, client, create=False)
    folder = dirs["denied"]
    if not folder.is_dir():
        raise ws.WorkspaceError("approvals/denied does not exist; the workspace owner creates it for the "
                                "approver account")
    # Fix round 1, item 3: the atomic, race-closing check, right before publish.
    # The scan-based F21 check above already refused a request a grant had
    # already reached disk for; this instead closes the window where a grant()
    # or _grant_delegated() call racing this one has not written its apr-*.json
    # file yet but wins the shared decision-<request_id> marker first. A repeat
    # denial (this same side, winning or losing the race against itself) always
    # sees outcome == "denied" here and proceeds normally.
    denial_id = "dny-" + secrets.token_hex(6)
    decision = _claim_decision(dirs, request_id, "denied", denial_id)
    if decision["outcome"] != "denied":
        raise delegation.Refusal("already-granted", f"{request_id} already has a granted approval; nothing "
                                                    "was denied")
    t = now if now is not None else time.time()
    record = {"schema": DENIAL_SCHEMA, "id": denial_id, "request_id": request_id,
              "request_sha256": sha, "client": req["client"], "change": req.get("change"),
              "reason_class": reason_class, "reason": printable(reason.strip())[:500], "approver": actor.account,
              "approver_uid": actor.uid, "approver_kind": actor.kind, "approver_model": actor.model,
              "delegated": True, "denied_at": _iso(t)}
    path = folder / f"{record['id']}.json"
    ws._write_json(path, record)
    path.chmod(0o644)
    return record


DENIAL_REQUIRED = ("schema", "id", "request_id", "request_sha256", "client", "change", "reason_class", "reason",
                   "approver", "approver_uid", "approver_kind", "approver_model", "delegated", "denied_at")


def delegated_denials(workspace, client, *, config=None, control_stat=None) -> list[dict]:
    """Denials the workspace's delegated approver published, trusted the same way
    a grant is. Fails closed on anything that cannot be verified, rather than
    silently reporting no denials: a caller (an owner grant among them) that
    treated an untrustworthy "no denials" as ground truth could grant a request a
    delegated approver already denied.

    Fix round 1, item 1: R46 (the workspace root, clients/, the client folder,
    workspace.json and consent.json must not belong to or be writable by the
    approver account) runs first, the same check and the same call-scoped
    `control_stat` seam `find_by_idempotency_key`/`_granted_for` use; a violation
    raises `delegation.Refusal("not-delegated", ...)` instead of returning [].

    Fix round 1, item 2: a dny-*.json file that exists but cannot be read,
    parsed, or is missing a required field also raises
    (`delegation.Refusal("denial-unreadable", ...)`), never treated as absent.
    The pre-existing missing-`denied`-folder case (no delegated denial has ever
    been written for this client, or the delegate/config do not even name this
    account as approver) stays silent (`[]`): that is a provisioning state, not
    an untrustworthy one. A denial file that reads fine and has every required
    field, but does not itself belong to the account workspace.json currently
    names as approver (for example: workspace.json was edited to name a
    different approver_uid than the one that actually wrote the file), is still
    filtered out silently: it is simply not an authentic denial from the account
    currently named, not evidence the control files cannot be trusted."""
    config = config if config is not None else _config(workspace)
    item = delegation.delegate_for(config, "approver")
    dirs = _dirs(workspace, client, create=False)
    folder = dirs["denied"]
    if item is None or item["uid"] != config.get("approver_uid") or not folder.is_dir():
        return []
    approver = config.get("approver_uid")
    if config.get("approval_verify") == "owner-uid" and type(approver) is int:
        try:
            root = ws.load_workspace(workspace)[0]
        except (OSError, ws.WorkspaceError) as exc:
            raise delegation.Refusal("not-delegated", f"cannot verify the workspace's control files: {exc}") \
                from None
        controls = _controls_problem(root, dirs["client"], approver, control_stat=control_stat)
        if controls:
            raise delegation.Refusal("not-delegated", controls)
    out = []
    for path in sorted(folder.glob("dny-*.json")):
        record = _read(path)
        if record is None or record.get("schema") != DENIAL_SCHEMA or any(k not in record for k in DENIAL_REQUIRED):
            raise delegation.Refusal("denial-unreadable",
                                     f"{path} exists but cannot be trusted as a denial; nothing was decided")
        if not _approver_owned(path, config) and path.name == f"{record.get('id')}.json" \
                and record.get("client") == ws.slug_for(client):
            out.append(record)
    return out


def _delegated_denial(workspace, client, request_id, *, control_stat=None) -> dict | None:
    return next((d for d in delegated_denials(workspace, client, control_stat=control_stat)
                if d.get("request_id") == request_id), None)


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
        # F28: the stat-based ownership test is shared (_owner_mismatch), the same
        # one D7's idempotency lookup trusts a marker or a granted file by
        # (_approver_owned); the two a15 messages below stay exact (fix round 1
        # finding 1: no test anywhere pins their text, but the ruling does).
        if _owner_mismatch(st, approver):
            return "the approval file's owner is not the approver account, or others can write it"
        if _owner_mismatch(path.parent.stat(), approver):
            return "approvals/granted must be owned by the approver account and writable only by it"
        layout = _granted_layout(path, client)
        if layout is None:
            return "the approval file is not in the workspace's clients/<client>/approvals/granted layout"
        controls = _controls_problem(*layout, approver)
        if controls:
            return controls
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
    identity = _identity_problem(record, config, verify)
    if identity:
        return identity
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


HUMAN_GRANT_REFUSAL = ("this workspace's approver account belongs to an AI delegate, so it takes no owner "
                       "(human) approvals; production approvals need a workspace whose approver is a person")


def _ai_approver(config: dict) -> bool:
    """R45: the workspace names an AI approver delegate, so its approver account is
    the AI's, and a human-kind record from that account cannot be told from a forged
    one. Such a workspace takes no owner (human) approvals. Fails closed: an
    approver entry (or delegates value) that cannot be read counts as AI."""
    delegates = config.get("delegates")
    if delegates is None:
        return False
    if not isinstance(delegates, dict):
        return True
    if "approver" not in delegates:
        return False
    item = delegation.delegate_for(config, "approver")
    return item is None or item["kind"] == "ai"


def _control_stat(path, st=None):
    """The default owner-and-mode lookup for a control file or folder: `st` when
    the caller already holds the fstat of a protected read, else lstat (a symlink
    is seen, never followed). Every caller below takes its own `control_stat`
    override in place of this (fix round 1); a caller that passes none gets this
    one, read fresh from the module each time, so a test's monkeypatch of
    `approval._control_stat` still reaches a call that names no override."""
    return st if st is not None else os.lstat(path)


def _control_problem(path, approver, st=None, *, control_stat=None) -> str:
    """R46: why a control file (workspace.json, a client's consent.json) cannot be
    trusted in a tier 2 workspace; "" when it can. The approver account must not
    own it, nobody but its owner may write it, and it must be a regular file.
    `control_stat` (fix round 1), when given, replaces `_control_stat` for this
    one call only; the default is looked up on the module at call time, not
    captured at import time, so an unrelated monkeypatch of the global (kept for
    the gate's own R46 check, which has no call-scoped seam of its own) still
    applies to a caller that passes no override."""
    try:
        found = (control_stat or _control_stat)(path, st)
    except OSError:
        return f"cannot read {path}; control files must not be owned or writable by the approver account"
    if not stat.S_ISREG(found.st_mode) or found.st_uid == approver or found.st_mode & 0o022:
        return (f"{path} is owned by the approver account, writable by others or not a regular file; control "
                "files must not be owned or writable by the approver account")
    return ""


def _folder_problem(path, approver, *, control_stat=None) -> str:
    """R46 for a folder holding a control file: a real folder (not a link), not the
    approver account's, and, when others can write it, sticky (S_ISVTX), so nobody
    can rename or replace an entry they do not own. `control_stat`: see
    `_control_problem`."""
    try:
        found = (control_stat or _control_stat)(path)
    except OSError:
        return f"cannot read {path}; control folders must not be owned or writable by the approver account"
    if not stat.S_ISDIR(found.st_mode) or found.st_uid == approver:
        return (f"{path} is owned by the approver account or is not a real folder; control folders must not be "
                "owned or writable by the approver account")
    if found.st_mode & 0o022 and not found.st_mode & stat.S_ISVTX:
        return (f"{path} is writable by others without the sticky bit; control folders must not be owned or "
                "writable by the approver account")
    return ""


def _controls_problem(root: Path, client_folder: Path, approver, workspace_st=None, *, control_stat=None) -> str:
    """R46: the workspace root, clients/ and the client's folder, then workspace.json
    (from `workspace_st`, the fstat of a protected read, when given) and the
    client's consent.json. `control_stat` (fix round 1): an injectable override
    for the owner lookup every one of those checks makes, in place of mutating
    the process-global `approval._control_stat`, so one caller (a delegated
    grant that cannot actually chown the workspace) faking ownership for its own
    check can never also change what a different, concurrent caller in the same
    process sees through the untouched default. `_grant_delegated` (the only
    production caller that needs this) threads its own `control_stat` through
    here; `_problem` (the gate's R46 check, run when a granted approval is
    consumed) passes none, because no real caller of the gate needs to fake
    ownership, only tests do, and they still do it by monkeypatching the
    default."""
    for folder in (root, root / "clients", client_folder):
        problem = _folder_problem(folder, approver, control_stat=control_stat)
        if problem:
            return problem
    return (_control_problem(root / ws.CONFIG, approver, workspace_st, control_stat=control_stat)
            or _control_problem(client_folder / consent.FILE, approver, control_stat=control_stat))


def _granted_layout(path: Path, client: str) -> tuple[Path, Path] | None:
    """(workspace root, client folder) for an approval file at
    <root>/clients/<client>/approvals/granted/<id>.json, the layout _dirs builds;
    None when the file is anywhere else, so a layout change cannot point the R46
    check at other folders."""
    granted = path.parent
    approvals = granted.parent
    folder = approvals.parent
    clients = folder.parent
    if granted.name != "granted" or approvals.name != "approvals" or folder.name != client \
            or clients.name != "clients":
        return None
    return clients.parent, folder


def _identity_problem(record: dict, config: dict, verify: str) -> str:
    """The gate's acceptance rule for who granted an approval: a recorded kind on
    every record; a human (owner) grant names no model; an AI or other delegated
    grant only in a tier 2 workspace whose delegated approver (same account, same
    kind) made it, and never for a production or unknown org."""
    kind, is_delegated, model = record.get("approver_kind"), record.get("delegated"), record.get("approver_model")
    if kind not in delegation.KINDS or type(is_delegated) is not bool:
        return "malformed approval"
    if kind == "ai" and (not isinstance(model, str) or not delegation.MODEL_RE.fullmatch(model)):
        return "malformed approval"
    if kind == "human" and model is not None:
        return "malformed approval"
    if not is_delegated:
        if kind != "human":
            return "an AI approval must come from the workspace's delegated approver"
        if _ai_approver(config):
            return HUMAN_GRANT_REFUSAL
        return ""
    item = delegation.delegate_for(config, "approver")
    if verify != "owner-uid" or not delegation.delegated_tier2(config) or item is None \
            or item["kind"] != kind or record.get("approver_uid") != item["uid"] \
            or record.get("approver") != item["account"]:
        return "a delegated approval needs a tier 2 workspace whose delegated approver made it"
    if record.get("org_kind") not in NONPRODUCTION:
        return "a delegated approval is never valid for a production or unknown org"
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
        "approver": record.get("approver"), "approver_uid": record.get("approver_uid"),
        "approver_kind": record.get("approver_kind"), "approver_model": record.get("approver_model"),
        "delegated": record.get("delegated"), "before_state": record.get("before_state"),
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
    if record.get("delegated") is not False and entry.get("kind") not in NONPRODUCTION:
        return "a delegated approval is never valid for a production or unknown org"
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


def _exclusive(path: Path, value: dict) -> bool:
    """Create path only if it does not exist (the atomic step of every claim)."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(value))
    return True


def _read(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _same_folder(record: dict, cwd) -> bool:
    here = os.path.realpath(str(cwd if cwd is not None else os.getcwd()))
    return record.get("cwd") is None or here == record.get("cwd")


def _wrapper_matches(workspace, client, invocation, org_alias, *, window, now, config, cwd):
    dirs = _dirs(workspace, client)
    head, words = invocation
    for path, record in _granted(dirs):
        if record.get("org_alias") != org_alias or record.get("kind") != "command":
            continue
        found = _consumed(dirs, record)
        if found is None:
            continue
        marker, used = found
        if now - _epoch(used["at"]) > window or (dirs["consumed"] / f"{record['id']}.wrapper").exists():
            continue
        if command_words(str(record.get("command"))) != (head, list(words)):
            continue
        if not _same_folder(record, cwd):
            continue
        if not _authentic(workspace, client, path, record, used, config):
            continue
        yield path, record, marker, used


def consumed_for_wrapper(workspace, client, invocation: tuple[str, list[str]], org_alias, *,
                         window=WRAPPER_WINDOW, now=None, config=None, cwd=None) -> dict | None:
    """The approval the gate consumed in the last `window` seconds for this exact
    Torque route invocation, run from the approved folder (`cwd`, default the current
    one), authenticated again, with its files checked again in full, claimed
    atomically so it serves one wrapper run."""
    now = now if now is not None else time.time()
    config = config if config is not None else _config(workspace)
    dirs = _dirs(workspace, client)
    for _path, record, _marker, _used in _wrapper_matches(workspace, client, invocation, org_alias,
                                                            window=window, now=now, config=config, cwd=cwd):
        if record.get("payload_argv"):
            digest, _ = payload_digest(record["payload_argv"], Path(str(record.get("cwd"))), capped=False)
            if digest != record.get("payload_digest"):
                continue
        if _exclusive(dirs["consumed"] / f"{record['id']}.wrapper", {"at": _iso(now), "cwd": record.get("cwd")}):
            return record
    return None


MAX_RELEASES = 3


def _releases(dirs: dict, approval_id: str) -> int:
    pattern = re.compile(re.escape(approval_id) + r"\.released-\d+$")
    return sum(1 for p in dirs["consumed"].iterdir() if pattern.match(p.name))


def _release(dirs: dict, approval_id: str, why: str, org_alias: str) -> bool:
    """Return a consumed approval (nothing ran), keeping its markers for the record."""
    count = _releases(dirs, approval_id)
    if count >= MAX_RELEASES:
        return False
    base = dirs["consumed"] / approval_id
    for suffix in (".wrapper", ".child"):
        side = base.with_name(approval_id + suffix)
        if side.exists():
            os.replace(side, base.with_name(f"{approval_id}.released-{count + 1}{suffix.replace('.', '-')}"))
    os.replace(base, base.with_name(f"{approval_id}.released-{count + 1}"))
    try:
        _activity(dirs, {"action": f"released: {why}", "approval_id": approval_id, "org_alias": org_alias})
    except OSError:
        pass
    return True


def release_for_retry(workspace, client, invocation: tuple[str, list[str]], org_alias, *,
                      window=WRAPPER_WINDOW, now=None, config=None, cwd=None) -> bool:
    """After the wrapper could not resolve the org (nothing ran), return the approval
    the gate just consumed for this exact invocation, so the same command can be run
    again inside its window. At most three times per approval; each release is logged."""
    now = now if now is not None else time.time()
    config = config if config is not None else _config(workspace)
    dirs = _dirs(workspace, client)
    for _path, record, _marker, _used in _wrapper_matches(workspace, client, invocation, org_alias,
                                                            window=window, now=now, config=config, cwd=cwd):
        return _release(dirs, record["id"], "the org could not be resolved", org_alias)
    return False


PARENT_WINDOW = 1800


def authorize_child(workspace, client, approval_id, child_words: list[str]) -> None:
    """The revert executor names the one wrapper command it will start for the
    approval its own run verified (once per verified run)."""
    if not _valid_id(approval_id, "apr-"):
        raise ws.WorkspaceError("invalid approval ID")
    dirs = _dirs(workspace, client)
    if _read(dirs["consumed"] / f"{approval_id}.wrapper") is None:
        raise ws.WorkspaceError("the approval was not verified by the parent run")
    if not _exclusive(dirs["consumed"] / f"{approval_id}.child", {"child": list(child_words)}):
        raise ws.WorkspaceError("a child command was already named for this approval")


def _parent_state(workspace, client, approval_id, org_alias, invocation, *, window, now, config, cwd):
    if not _valid_id(approval_id, "apr-") or invocation is None:
        return None
    dirs = _dirs(workspace, client)
    path = dirs["granted"] / f"{approval_id}.json"
    record = _read(path)
    found = _consumed(dirs, record) if record else None
    wrapper = _read(dirs["consumed"] / f"{approval_id}.wrapper")
    child = _read(dirs["consumed"] / f"{approval_id}.child")
    if not found or not wrapper or not child:
        return None
    try:
        started = _epoch(wrapper["at"])
    except (KeyError, TypeError, ValueError):
        return None
    if record.get("org_alias") != org_alias or now - started > window or child.get("child") != list(invocation[1]):
        return None
    if not _same_folder(record, cwd) or not _authentic(workspace, client, path, record, found[1], config):
        return None
    return dirs, record


def approved_parent(workspace, client, approval_id, org_alias, invocation=None, *, window=PARENT_WINDOW,
                    now=None, config=None, cwd=None) -> dict | None:
    """For the wrapper the revert executor starts: the approval its parent verified in
    the last `window` seconds, authenticated again, for exactly the command the parent
    named, run from the approved folder, claimed atomically once."""
    now = now if now is not None else time.time()
    config = config if config is not None else _config(workspace)
    state = _parent_state(workspace, client, approval_id, org_alias, invocation, window=window, now=now,
                          config=config, cwd=cwd)
    if state is None:
        return None
    dirs, record = state
    if not _exclusive(dirs["consumed"] / f"{approval_id}.child-used", {"at": _iso(now)}):
        return None
    return record


def release_child(workspace, client, approval_id, org_alias, invocation, *, window=PARENT_WINDOW, now=None,
                  config=None, cwd=None) -> bool:
    """The revert's child could not resolve the org (nothing ran): return the parent's
    approval, so the same `torque recover` command can be run again in its window."""
    now = now if now is not None else time.time()
    config = config if config is not None else _config(workspace)
    state = _parent_state(workspace, client, approval_id, org_alias, invocation, window=window, now=now,
                          config=config, cwd=cwd)
    if state is None:
        return False
    dirs, record = state
    if (dirs["consumed"] / f"{approval_id}.child-used").exists():
        return False
    return _release(dirs, approval_id, "the recovery child could not resolve the org", org_alias)


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


def decision(workspace, client, request_id, *, now=None, config=None) -> tuple[str, dict]:
    """Where a request stands, for the waiter (task D10, spec requirement 21) and
    any other read-only caller: `granted`, `denied`, `expired` or `pending`.
    Read-only throughout, so the agent account can run it directly: nothing here
    claims, consumes or writes a file (no `_claim`, no `_activity`, no grant or
    denial is ever created).

    A denial always wins over a grant, and is trusted only through the same two
    fail-closed readers everything else in this module uses: the delegated
    approver's file (`_delegated_denial`, which runs R46 and raises
    `delegation.Refusal` on an unreadable/malformed denial file or a workspace
    whose control files could have been forged by the approver account, rather
    than silently reporting "no denial") and the consultant's own change-record
    `approval_deny` event. Neither is a bare file existence check.

    A grant is trusted only when it passes the exact authenticity checks the
    gate applies before consuming it (`_problem`): record shape, ownership,
    R46 on the workspace's control files and folders, and the signature or
    delegated-identity rule. A record that merely sits in approvals/granted/
    but fails any of those (forged, unowned, malformed, wrong client) is never
    read as granted; it falls through exactly as if it were not there.

    F11 / R47 (fix round 1): a request whose own REQUEST_TTL plus SKEW has
    passed with no surviving, unused grant is `expired`, the same tolerance the
    grant path itself gives a request (`_grant_delegated` accepts a request up
    to SKEW seconds in the future and refuses only past `created + REQUEST_TTL
    + SKEW`), so the waiter never calls a request expired while the grant path
    could still accept and grant it. A genuine grant whose own TTL expired
    unused is `expired` too. A grant that was used before it expired is still
    `granted` (it happened), even if it has since expired.

    Reads only approvals bound to this exact `request_id`; a grant or denial
    for a different request in the same client is skipped, never reported."""
    now = now if now is not None else time.time()
    config = config if config is not None else _config(workspace)
    req = load_request(workspace, client, request_id)
    slug = ws.slug_for(client)
    denial = _delegated_denial(workspace, client, request_id)
    if denial:
        return "denied", {k: denial.get(k) for k in ("id", "reason_class", "reason", "approver", "approver_kind",
                                                       "approver_model", "denied_at")}
    events = changes.get_change(workspace, client, req["change"])["events"]
    owner = next((e for e in events if e["kind"] == "approval_deny" and e.get("request_id") == request_id), None)
    if owner:
        return "denied", {"reason_class": "owner-denied", "reason": owner.get("reason"),
                          "approver": owner.get("approver"), "approver_kind": "human"}
    dirs = _dirs(workspace, client, create=False)
    expired = False
    for path, record in _granted(dirs):
        if record.get("request_id") != request_id:
            continue
        used = (dirs["consumed"] / str(record.get("id"))).is_file()
        problem = _problem(record, path, config, slug, now)
        if problem == "approval expired" and not used:
            expired = True
            continue
        if problem and not (used and problem == "approval expired"):
            continue
        return "granted", {"approval_id": record["id"], "expires_at": record["expires_at"], "used": used,
                           "approver": record.get("approver"), "approver_kind": record.get("approver_kind")}
    created = req.get("created_at")
    # R47 (fix round 1): + SKEW matches the grant path's own tolerance for a
    # request's age (_grant_delegated refuses only past created + REQUEST_TTL +
    # SKEW), so the waiter never reports "expired" for a request the grant path
    # could still accept and grant.
    if expired or (created and now > _epoch(created) + REQUEST_TTL + SKEW):
        return "expired", {"why": "the approval's window ended unused" if expired else "the request is older "
                                                                                      "than one hour"}
    return "pending", {}


def wait_for_decision(workspace, client, request_id, seconds, *, poll=1.0, clock=time.time,
                      sleep=time.sleep) -> tuple[str, dict]:
    """Bounded polling for `decision()` (spec requirement 21): an unattended agent
    session waiting on a pending request gets `granted`, `denied` or `expired`
    as soon as `decision()` reports one; a still-`pending` request is checked
    again, no more often than every `poll` seconds, until `seconds` have
    elapsed, then `timeout`. Never a busy loop: every `sleep` call between reads
    is for a strictly positive duration (the deadline check above it already
    returns before `sleep` could ever be asked for zero or a negative wait), and
    the loop only ever re-reads `decision()`, never writes anything.

    Raises `ws.WorkspaceError` for `seconds` outside 0 to WAIT_MAX (a usage
    error, not one of `granted`/`denied`/`expired`/`timeout`). Propagates
    `delegation.Refusal` from `decision()` unchanged: an R46 violation or an
    unreadable denial file is not a decision this function can make, so it is
    not silently folded into `pending` or `timeout`; see
    `cli_approval._status` for how `approval status --wait` turns that into
    its own exit 2 rather than any of WAIT_CODES."""
    if type(seconds) is not int or not 0 <= seconds <= WAIT_MAX:
        raise ws.WorkspaceError(f"--wait takes 0 to {WAIT_MAX} seconds")
    deadline = clock() + seconds
    while True:
        state, detail = decision(workspace, client, request_id, now=clock())
        if state != "pending":
            return state, detail
        if clock() >= deadline:
            return "timeout", {"waited_seconds": seconds}
        sleep(min(poll, max(0.0, deadline - clock())) or poll)
