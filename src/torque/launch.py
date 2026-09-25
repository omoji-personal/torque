"""Launch bindings and launch records for connected sessions.

A connected session is bound to one client only by a launch record that `torque
launch` writes for the process it becomes (exec keeps the pid): after the
consultant's presence check (`via: presence`), or, in a tier 2 workspace with a
delegated approver, after claiming a single-use launch binding that approver's
account wrote (`via: binding`). Doctor's probes write their own (`via: probe`).
The gate takes the binding from that record, never from TORQUE_CLIENT alone.

A binding is verified like a grant: the approver account owns it and its folder
and nobody else can write either, it is not a link, the workspace's control files
pass R46, and anything unreadable refuses. docs/delegated-approver.md."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import time

from . import workspace as ws

BINDING_SCHEMA = "torque.launch-binding/1"
LAUNCH_SCHEMA = "torque.launch/1"
BINDING_TTL_MAX = 900
LAUNCH_ID = re.compile(r"(lnk|launch|probe)-[0-9a-f]{12}\Z")
BINDING_ID = re.compile(r"lnk-[0-9a-f]{12}\Z")
NONCE = re.compile(r"[0-9a-f]{32}\Z")
# F7: the launch-record ancestry check makes at most 8 `ps` calls in total: up to
# MAX_ANCESTORS - 1 for the walk (the parent pid is free) plus one for the start time.
MAX_PS_CALLS = 8
MAX_ANCESTORS = 7
VIA = ("binding", "presence", "probe")
PS_TIMEOUT = 2
# R49 (amended): the only claude options a delegated launch passes through, an
# allowlist (fail closed). Options that take a value take the next word or `=value`;
# --permission-mode only with the value "default".
ALLOWED_FLAGS = ("-p", "--print", "--include-partial-messages", "--replay-user-messages", "--verbose",
                 "--no-session-persistence")
ALLOWED_VALUE_OPTIONS = ("--input-format", "--output-format", "--model", "--fallback-model", "--effort",
                         "--append-system-prompt", "--max-budget-usd", "--json-schema", "--session-id", "--name")
# Removed from a delegated launch's child environment (and refused when the caller sets it).
REFUSED_ENV = ("CLAUDE_CODE_SIMPLE",)
BINDING_FIELDS = ("schema", "id", "workspace", "client", "nonce", "created_at", "expires_at", "approver",
                  "approver_uid", "approver_kind", "approver_model")


def _ps(argv: list[str]) -> str:
    """One `ps` call. Inside a gate call it is charged to the gate's per-call budget
    (BudgetExceeded past it) and never waits past what is left of it. The C locale
    keeps `lstart` in the English form `_lstart_epoch` reads."""
    from . import gate
    gate._spend()
    left = gate._remaining()
    wait = PS_TIMEOUT if left is None else min(PS_TIMEOUT, left)
    if wait <= 0:
        raise gate.BudgetExceeded(f"the gate's {gate.GATE_TIME_BUDGET:g}-second time budget for one call ran out")
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=wait,
                              env={**os.environ, "LC_ALL": "C"}).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _lstart_epoch(text) -> float | None:
    """The epoch of a `ps -o lstart=` start time (local time, one-second
    precision), or None when it is not one."""
    if not isinstance(text, str):
        return None
    try:
        return time.mktime(time.strptime(" ".join(text.split()), "%a %b %d %H:%M:%S %Y"))
    except (ValueError, OverflowError):
        return None


def process_start(pid: int, *, run=None) -> str | None:
    """The process's start time as `ps` prints it (one `ps` call), or None."""
    if os.name == "nt":
        return None
    out = (run or _ps)(["ps", "-o", "lstart=", "-p", str(pid)])
    return " ".join(str(out or "").split()) or None


def ancestor_pids(limit: int = MAX_ANCESTORS, *, stop=None, getppid=None, run=None) -> list[int]:
    """This process's ancestors, nearest first, at most `limit` of them. Lazy: the
    walk ends at `stop` (the pid being looked for) and at pid 1 or an unreadable
    step, and makes at most `limit - 1` `ps` calls (the parent pid needs none)."""
    chain: list[int] = []
    pid = (getppid or os.getppid)()
    while pid > 1 and len(chain) < limit:
        chain.append(pid)
        if pid == stop or len(chain) == limit:
            break
        out = str((run or _ps)(["ps", "-o", "ppid=", "-p", str(pid)]) or "").strip()
        if not out.isdigit():
            break
        pid = int(out)
    return chain


def launch_process_problem(pid, pid_started, *, getpid=None, getppid=None, run=None) -> str:
    """"" when `pid` is this process or one of its nearest ancestors and still has
    the start time `pid_started`; else why not. At most MAX_PS_CALLS `ps` calls."""
    if type(pid) is not int or pid <= 1 or not isinstance(pid_started, str) or not pid_started:
        return "the launch record names no process"
    if pid != (getpid or os.getpid)() \
            and pid not in ancestor_pids(MAX_ANCESTORS, stop=pid, getppid=getppid, run=run):
        return "the launch record belongs to another process"
    if process_start(pid, run=run) != pid_started:
        return "the launch record's process has been replaced"
    return ""


def _refuse(reason_class: str, message: str):
    from .delegation import Refusal
    return Refusal(reason_class, message)


def _started(pid: int, starts) -> str | None:
    """The launching process's start time; fails closed on POSIX when unreadable
    (a record without one could match any later process)."""
    found = (starts or process_start)(pid)
    if os.name != "nt" and (not isinstance(found, str) or not found):
        raise ws.WorkspaceError("cannot read this process's start time for the launch record; nothing was started")
    return found


def _record(root: Path, slug: str, ident: str, kind: str, via: str, pid: int, started, extra: dict) -> dict:
    from .approval import _iso
    return {"schema": LAUNCH_SCHEMA, "id": ident, "kind": kind, "via": via, "client": slug,
            "workspace": str(root), "pid": pid, "pid_started": started, "created_at": _iso(time.time()), **extra}


def _write_record(dirs: dict, record: dict) -> bool:
    from . import approval
    try:
        return approval._exclusive(dirs["consumed"] / f"{record['id']}.launch", record)
    except OSError as exc:
        raise ws.WorkspaceError(f"could not write the launch record: {exc}") from None


def _tier2_config(workspace, *, env=None, ancestors=None, getuid=None, control_stat=None, client=None):
    """The delegated-launch checks shared by `claim_binding` (and, before any
    presence-free step, by `cli_approval.launch`): not inside an AI session,
    POSIX, a connected tier 2 workspace whose approver is its named delegate, a
    launching account other than the approver's, and (with `client`) R46 on the
    control files. Returns (root, config, client folder or None)."""
    from . import approval, delegation, gate
    from .presence import agent_reason
    why = agent_reason(env, ancestors)
    if why:
        raise _refuse("agent-session", f"a session is never launched from inside an AI session: {why}")
    if not hasattr(os, "getuid"):
        raise _refuse("tier-2-required", "a delegated launch needs tier 2 approvals (not available on Windows)")
    root = Path(workspace).expanduser().resolve()
    config, config_st = delegation._read_protected_config(root)
    if gate._resolve_ai_access(config.get("ai_access"), config.get("approval")) != "connected":
        raise _refuse("tier-2-required", "a delegated launch is for a connected workspace with tier 2 "
                                         "(owner-uid) approvals")
    if config.get("approval_verify") != "owner-uid":
        raise _refuse("tier-2-required", "a delegated launch needs tier 2 (owner-uid) approvals; this workspace "
                                         "uses tier 1")
    if not delegation.delegated_tier2(config):
        raise _refuse("not-delegated", "a delegated launch is only for a workspace whose approver account is its "
                                       "named approver delegate")
    if (getuid or os.getuid)() == config["approver_uid"]:
        raise _refuse("not-delegated", "a delegated launch runs as the agent's account, never as the approver "
                                       "account (it could then write its own bindings and grants)")
    folder = None
    if client is not None:
        try:
            folder = ws.load_client(root, client)[0]
        except (OSError, ws.WorkspaceError) as exc:
            raise _refuse("not-delegated", f"cannot read the client folder: {exc}") from None
        controls = approval._controls_problem(root, folder, config["approver_uid"], workspace_st=config_st,
                                              control_stat=control_stat)
        if controls:
            raise _refuse("not-delegated", controls)
    return Path(os.path.realpath(root)), config, folder


def create_binding(workspace, client, *, model_id, minutes=10, env=None, ancestors=None, getuid=None, now=None,
                   root_owner=None, control_stat=None) -> dict:
    """The delegated approver's single-use launch binding for one client, written
    by (and so owned by) the approver account to approvals/granted/lnk-<12hex>.json,
    mode 0644 so the launching account can read it. Never creates a folder: the
    approvals folders belong to whoever the layout says. `root_owner` and
    `control_stat` are the caller-proof (R41) and R46 test seams."""
    from . import approval, delegation
    actor, config, root, config_st = delegation._delegated_proof(
        workspace, "approver", model_id=model_id, getuid=getuid, env=env, ancestors=ancestors,
        root_owner=root_owner)
    if not delegation.delegated_tier2(config):
        raise _refuse("tier-2-required", "a launch binding needs tier 2 (owner-uid) approvals and the workspace's "
                                         "approver account as its named approver")
    if type(minutes) is not int or not 1 <= minutes * 60 <= BINDING_TTL_MAX:
        raise ws.WorkspaceError(f"a launch binding lasts 1 to {BINDING_TTL_MAX // 60} minutes")
    try:
        folder = ws.load_client(root, client)[0]
    except (OSError, ws.WorkspaceError) as exc:
        raise _refuse("not-delegated", f"cannot read the client folder: {exc}") from None
    controls = approval._controls_problem(root, folder, config["approver_uid"], workspace_st=config_st,
                                          control_stat=control_stat)
    if controls:
        raise _refuse("not-delegated", controls)
    try:
        approval._usable_consent(root, client)
    except ws.WorkspaceError as exc:
        raise _refuse("consent-unusable", str(exc)) from None
    dirs = approval._dirs(root, client, create=False)
    try:
        granted_st = dirs["granted"].lstat()
    except OSError:
        raise _refuse("not-delegated", f"{dirs['granted']} does not exist; the approver account's approvals "
                                       "folder is set up with the workspace") from None
    if not stat.S_ISDIR(granted_st.st_mode) or approval._owner_mismatch(granted_st, config["approver_uid"]):
        raise _refuse("not-delegated", f"{dirs['granted']} must be the approver account's folder and writable "
                                       "only by it")
    t = now if now is not None else time.time()
    record = {"schema": BINDING_SCHEMA, "id": "lnk-" + secrets.token_hex(6), "workspace": str(root),
              "client": ws.slug_for(client), "nonce": secrets.token_hex(16), "created_at": approval._iso(t),
              "expires_at": approval._iso(t + minutes * 60), "approver": actor.account, "approver_uid": actor.uid,
              "approver_kind": actor.kind, "approver_model": actor.model}
    path = dirs["granted"] / f"{record['id']}.json"
    ws._write_json(path, record)
    path.chmod(0o644)
    return record


def _read_binding(path: Path, approver: int) -> dict:
    """Open the binding once, without following a link, and take its owner, mode
    and content from that descriptor; the folder must be the approver's too."""
    from . import approval
    invalid = "the launch binding is not the approver account's, or others can write it or its folder"
    try:
        path.lstat()
    except FileNotFoundError:
        raise _refuse("binding-missing", f"no launch binding {path.stem} for this client") from None
    except OSError:
        raise _refuse("binding-invalid", f"cannot read the launch binding {path.stem}") from None
    try:
        folder = path.parent.lstat()
    except OSError:
        raise _refuse("binding-invalid", invalid) from None
    if not stat.S_ISDIR(folder.st_mode) or approval._owner_mismatch(folder, approver):
        raise _refuse("binding-invalid", invalid)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        raise _refuse("binding-invalid", f"the launch binding {path.stem} is a link or cannot be read") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or approval._owner_mismatch(st, approver):
            raise _refuse("binding-invalid", invalid)
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = None
            text = handle.read()
    except OSError:
        raise _refuse("binding-invalid", f"cannot read the launch binding {path.stem}") from None
    except ValueError:
        raise _refuse("binding-invalid", "malformed launch binding") from None
    finally:
        if fd is not None:
            os.close(fd)
    try:
        value = json.loads(text)
    except ValueError:
        value = None
    if not isinstance(value, dict):
        raise _refuse("binding-invalid", "malformed launch binding")
    return value


def _checked_binding(root: Path, config: dict, slug: str, binding_id: str, t: float) -> tuple[dict, float, float]:
    """The binding `binding_id` of this workspace and client, fully verified except
    for its expiry: `_read_binding` (approver-owned file and folder, no link, one
    descriptor), then its fields (shape, nonce, this workspace and client, the
    current approver delegate and model) and its dates (not in the future, window
    within BINDING_TTL_MAX + SKEW). Shared by the claim and by the gate's re-check
    (F8). Returns (binding, created, expires) as epochs."""
    from . import approval, delegation
    item = delegation.delegate_for(config, "approver")
    if item is None:
        raise _refuse("not-delegated", "this workspace names no approver delegate")
    path = approval._dirs(root, slug, create=False)["granted"] / f"{binding_id}.json"
    binding = _read_binding(path, config["approver_uid"])
    if binding.get("schema") != BINDING_SCHEMA or binding.get("id") != binding_id \
            or any(key not in binding for key in BINDING_FIELDS) \
            or not isinstance(binding.get("nonce"), str) or not NONCE.fullmatch(binding["nonce"]):
        raise _refuse("binding-invalid", "malformed launch binding")
    if binding.get("client") != slug or binding.get("workspace") != str(root):
        raise _refuse("binding-invalid", "the launch binding is for another workspace or client")
    kind, model = binding.get("approver_kind"), binding.get("approver_model")
    named = (binding.get("approver"), binding.get("approver_uid"), kind)
    if named != (item["account"], item["uid"], item["kind"]) \
            or (kind == "ai" and not (isinstance(model, str) and delegation.MODEL_RE.fullmatch(model))) \
            or (kind == "human" and model is not None):
        raise _refuse("binding-invalid", "the launch binding names a different approver than the workspace's "
                                         "delegated approver")
    try:
        created, expires = approval._epoch(binding["created_at"]), approval._epoch(binding["expires_at"])
    except (TypeError, ValueError):
        raise _refuse("binding-invalid", "malformed launch binding") from None
    if created > t + approval.SKEW:
        raise _refuse("binding-invalid", "the launch binding is dated in the future")
    if expires < created or expires - created > BINDING_TTL_MAX + approval.SKEW:
        raise _refuse("binding-invalid", f"the launch binding's window is longer than {BINDING_TTL_MAX // 60} "
                                         "minutes")
    return binding, created, expires


def claim_binding(workspace, client, binding_id, *, pid=None, now=None, starts=None, env=None, ancestors=None,
                  getuid=None, control_stat=None) -> dict:
    """Consume a launch binding for this process: the checks in `_tier2_config`,
    then the binding itself (approver-owned, unaltered, for this workspace and
    client, from the current approver delegate, within its window), then the
    single-use claim, an O_EXCL approvals/consumed/<binding_id>.launch record.
    Every refusal is a delegation.Refusal with a reason class."""
    from . import approval
    root, config, folder = _tier2_config(workspace, env=env, ancestors=ancestors, getuid=getuid,
                                         control_stat=control_stat, client=client)
    if not isinstance(binding_id, str) or not BINDING_ID.fullmatch(binding_id):
        # Never let near a path.
        raise _refuse("binding-invalid", "a launch binding ID looks like lnk-0123456789ab")
    slug = folder.name
    dirs = approval._dirs(root, client, create=False)
    t = now if now is not None else time.time()
    binding, _, expires = _checked_binding(root, config, slug, binding_id, t)
    kind = binding["approver_kind"]
    if t > expires:
        raise _refuse("binding-expired", "the launch binding expired; ask the approver for a new one")
    pid = pid if pid is not None else os.getpid()
    record = _record(root, slug, binding_id, kind, "binding", pid, _started(pid, starts),
                     {"binding_id": binding_id, "nonce": binding["nonce"],
                      "binding_created_at": binding["created_at"], "binding_expires_at": binding["expires_at"],
                      "approver": {k: binding[k] for k in ("approver", "approver_uid", "approver_kind",
                                                          "approver_model")}})
    # The launching (agent) account keeps approvals/consumed, as the gate does; it
    # is created here only when absent, and only once the binding has verified.
    try:
        dirs["consumed"].mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise ws.WorkspaceError(f"could not write the launch record: {exc}") from None
    if not _write_record(dirs, record):
        raise _refuse("binding-used", "the launch binding was already used; ask the approver for a new one")
    return record


def write_launch_record(workspace, client, kind, *, pid=None, starts=None) -> dict:
    """The launch record for a consultant's presence launch (`human`) or a doctor
    probe (`probe`). F1: the approvals folders are created when absent (mkdir 0700,
    an existing folder is never re-moded)."""
    from . import approval
    if kind not in ("human", "probe"):
        raise ws.WorkspaceError("launch record kind is human or probe")
    root, _ = ws.load_workspace(workspace)
    root = Path(os.path.realpath(root))
    folder = ws.load_client(root, client)[0]
    dirs = approval._dirs(root, client)
    pid = pid if pid is not None else os.getpid()
    prefix, via = ("launch", "presence") if kind == "human" else ("probe", "probe")
    record = _record(root, folder.name, f"{prefix}-{secrets.token_hex(6)}", kind, via, pid, _started(pid, starts),
                     {})
    if not _write_record(dirs, record):
        raise ws.WorkspaceError("could not write the launch record; try again")
    return record


def launch_flag_problem(extra) -> str:
    """R49 (amended): "" when every option passed through a delegated launch is on
    the allowlist (ALLOWED_FLAGS, ALLOWED_VALUE_OPTIONS with their value, and
    `--permission-mode default`); else the first refused one. Positional prompt text
    passes; a lone `--` passes but options after it are still refused (fail closed)."""
    words = [str(word) for word in extra or ()]
    index = 0
    while index < len(words):
        word = words[index]
        index += 1
        if word == "--" or not word.startswith("-"):
            continue
        name, eq, value = word.partition("=")
        if name in ALLOWED_FLAGS and not eq:
            continue
        if name in ALLOWED_VALUE_OPTIONS or name == "--permission-mode":
            if not eq:
                if index >= len(words):
                    return word
                value = words[index]
                index += 1
            if name == "--permission-mode" and value != "default":
                return f"--permission-mode {value}"
            continue
        return word
    return ""


def launch_env_problem(environ) -> str:
    """The first REFUSED_ENV variable set in `environ`, or ""."""
    return next((name for name in REFUSED_ENV if name in environ), "")


def _read_record(path: Path) -> dict | None:
    """A launch record read once through a descriptor that does not follow a link;
    None when it is missing, a link, not a regular file or not a JSON object."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = None
            value = json.loads(handle.read())
    except (OSError, ValueError):
        return None
    finally:
        if fd is not None:
            os.close(fd)
    return value if isinstance(value, dict) else None


# The record kind each `via` allows, and its ID prefix.
_VIA_KINDS = {"presence": (("human",), "launch-"), "probe": (("probe",), "probe-"),
              "binding": (("ai", "human"), "lnk-")}


def verify_launch(root, env, *, getpid=None, getppid=None, run=None, now=None) -> tuple[str | None, str]:
    """(the bound client slug, "") when `env` names, by TORQUE_LAUNCH, a launch record
    for its TORQUE_CLIENT and this workspace whose process is this one or one of its
    nearest ancestors with the same start time (`launch_process_problem`, at most
    MAX_PS_CALLS `ps` calls; skipped on Windows, F45) and, whenever the record names
    a binding (F8), whose approver binding still verifies in full and was claimed by
    a process started inside its window (R48); else (None, why). Nothing unreadable
    binds. `getpid`, `getppid`, `run` and `now` are test seams. A gate call past its
    time budget raises gate.BudgetExceeded (the gate then blocks the call)."""
    from . import approval
    launch_id, client = env.get("TORQUE_LAUNCH"), env.get("TORQUE_CLIENT")
    if not launch_id or not client:
        return None, "no launch record"
    if not isinstance(launch_id, str) or not LAUNCH_ID.fullmatch(launch_id):
        return None, "malformed launch ID"
    try:
        slug = ws.slug_for(client)
        root = Path(os.path.realpath(root))
        ws.load_workspace(root)
        folder = ws.load_client(root, slug)[0]
        path = approval._dirs(root, slug, create=False)["consumed"] / f"{launch_id}.launch"
    except (OSError, ValueError, ws.WorkspaceError):
        return None, "unreadable workspace or client"
    record = _read_record(path)
    if record is None:
        return None, "no readable launch record for this client (a link is not read)"
    via, kind = record.get("via"), record.get("kind")
    kinds, prefix = _VIA_KINDS.get(via, ((), None))
    if record.get("schema") != LAUNCH_SCHEMA or record.get("id") != launch_id or record.get("client") != slug \
            or record.get("workspace") != str(root) or kind not in kinds or not launch_id.startswith(prefix):
        return None, "the launch record is not for this client and workspace"
    try:
        record_created = approval._epoch(record.get("created_at"))
    except (TypeError, ValueError):
        return None, "malformed launch record"
    if os.name != "nt":
        why = launch_process_problem(record.get("pid"), record.get("pid_started"), getpid=getpid,
                                     getppid=getppid, run=run)
        if why:
            return None, why
    if via == "binding" or "binding_id" in record:
        why = _binding_problem(root, folder, slug, record, record_created, now)
        if why:
            return None, why
    return slug, ""


def _binding_problem(root: Path, folder: Path, slug: str, record: dict, record_created: float, now) -> str:
    """Why a binding-based launch record no longer binds; "" when it does."""
    from . import approval, delegation
    launch_id = record["id"]
    if record.get("binding_id") != launch_id:
        return "the launch record names a different binding, or none"
    if record.get("via") != "binding" or not BINDING_ID.fullmatch(launch_id) or not hasattr(os, "getuid"):
        return "a binding-based launch record needs a binding launch in a tier 2 workspace"
    try:
        config, config_st = delegation._read_protected_config(root)
    except ws.WorkspaceError:
        return "cannot read workspace.json to re-check the launch binding"
    if not delegation.delegated_tier2(config):
        return "the workspace no longer has a delegated approver for this launch's binding"
    controls = approval._controls_problem(root, folder, config["approver_uid"], workspace_st=config_st)
    if controls:
        return controls
    t = now if now is not None else time.time()
    try:
        binding, created, expires = _checked_binding(root, config, slug, launch_id, t)
    except ws.WorkspaceError as exc:
        return f"the launch's approver binding does not verify: {exc}"
    approver = {k: binding[k] for k in ("approver", "approver_uid", "approver_kind", "approver_model")}
    if record.get("nonce") != binding["nonce"] or record.get("kind") != binding["approver_kind"] \
            or record.get("binding_created_at") != binding["created_at"] \
            or record.get("binding_expires_at") != binding["expires_at"] or record.get("approver") != approver:
        return "the launch record does not match its approver binding"
    # A claim is made inside the binding's window (a late re-claim after deleting
    # the consumed marker is not).
    if record_created > min(created + BINDING_TTL_MAX, expires) + approval.SKEW:
        return "the launch record was made after its binding's window"
    if os.name != "nt":
        # R48: the claiming process started after the binding was created (a session
        # already running cannot adopt it) and within its window. The start time is
        # the one `ps` just confirmed, not the record's word.
        begun = _lstart_epoch(record.get("pid_started"))
        if begun is None:
            return "the launch record's process start time cannot be read"
        if begun < created - approval.SKEW:
            return "the launch's process started before its binding was created"
        if begun > expires + approval.SKEW:
            return "the launch's process started after its binding expired"
    return ""


def bound_env(environ, root, **kw) -> dict:
    """A copy of `environ` whose TORQUE_CLIENT is the verified slug when its launch
    record verifies (`verify_launch`), and absent otherwise: the gate binds a
    session only from its launch record, never from TORQUE_CLIENT alone."""
    env = dict(environ)
    slug, _ = verify_launch(root, env, **kw)
    if slug is None:
        env.pop("TORQUE_CLIENT", None)
    else:
        env["TORQUE_CLIENT"] = slug
    return env
