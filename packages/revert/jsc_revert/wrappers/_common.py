"""_common.py — shared infrastructure for wrapper subcommands.

Each wrapper follows the 3-phase template:
  1. Pre-snapshot: capture before-state, mint envelope, acquire lock
  2. Underlying: invoke sf CLI subprocess, capture exit + raw output
  3. Post-finalize: capture after-state, classify status, save manifest

The wrapper exit codes follow plan-v5:
  0  — full pipeline success
  10 — pre-snapshot failed; underlying NOT run (production safety preserved)
  11 — pre-snapshot failed; underlying ran (sandbox warn-mode)
  20 — underlying Failed (truly nothing changed)
  21 — underlying SucceededPartial → snapshot 'applied_partial'
  22 — underlying InProgress/Pending → wrapper polls
  23 — underlying ran async → snapshot 'async_unfinished'
  30 — post-finalize failed; manifest 'partial'
  40 — concurrency conflict
  50 — stale-revert blocked (--force needed)
  60 — TTL token invalid
"""

from __future__ import annotations

import json
from contextvars import ContextVar
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import threading
import uuid
from pathlib import Path
from typing import Any

from ..metadata_scope import captured_source_relative
from .. import bundle, manifest as mf, org_detect, org_sequence, revert_capabilities


EXIT_SUCCESS = 0
EXIT_PRESNAP_FAILED_PROD = 10
EXIT_PRESNAP_FAILED_SANDBOX = 11
EXIT_UNDERLYING_FAILED = 20
EXIT_SUCCEEDED_PARTIAL = 21
EXIT_IN_PROGRESS = 22
EXIT_ASYNC_UNFINISHED = 23
EXIT_POST_FINALIZE_FAILED = 30
EXIT_CONCURRENCY_CONFLICT = 40
EXIT_STALE_REVERT_BLOCKED = 50
EXIT_TOKEN_INVALID = 60
EXIT_NOT_APPROVED = 3  # connected mode: no consumed approval for this exact run

_ACTIVE_WRAPPER = ContextVar("torque_active_wrapper", default=None)
# Set by the revert executor for the wrapper it runs: the approval it verified.
APPROVED_PARENT_ENV = "TORQUE_APPROVED_PARENT"


class IndeterminateScope(Exception):
    """The workspace's mode could not be read, so connected mode cannot be ruled out."""


def _config_mode(path: Path, selected_client: bool = False):
    """The workspace.json at path: None when absent from a folder that is not a
    Torque workspace, else its parsed object. IndeterminateScope when it is present
    but unreadable or malformed, or absent where a workspace must have one (a
    selected client's firm folder, or a folder with Torque's workspace marker)."""
    config_path = path / "workspace.json"
    if not config_path.exists():
        if selected_client or ((path / "clients").is_dir() and (path / ".torque" / "templates.json").is_file()):
            raise IndeterminateScope(f"{config_path} is missing")
        return None
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise IndeterminateScope(f"{config_path} cannot be read ({exc})") from exc
    if not isinstance(config, dict):
        raise IndeterminateScope(f"{config_path} is not a JSON object")
    return config


def _is_connected(config) -> bool:
    return isinstance(config, dict) and config.get("ai_access") == "connected" and config.get("approval") == "required"


def _connected_scope():
    """(workspace, client slug or None) when this run is in a connected workspace:
    the one TORQUE_WORKSPACE selects, or one at or above the working folder (with the
    client from TORQUE_CLIENT). None when neither is connected. Raises
    IndeterminateScope when the selected workspace's configuration cannot be read."""
    root = os.environ.get("TORQUE_WORKSPACE")
    if root:
        path = Path(root).expanduser()
        client = None
        if (path / "client.json").is_file():
            client, path = path.name, path.parent.parent
        if _is_connected(_config_mode(path, selected_client=client is not None)):
            return path, client
    try:
        from torque import gate
        chain = gate._workspace_chain(Path(os.path.realpath(os.getcwd())))
    except ImportError:
        return None
    except OSError as exc:
        raise IndeterminateScope(f"the working folder's workspace cannot be read ({exc})") from exc
    for folder, mode, known in chain:
        if not known:
            raise IndeterminateScope(f"the workspace configuration at {folder} cannot be read")
        if mode == "connected":
            return folder, os.environ.get("TORQUE_CLIENT") or None
    return None


def _invocation() -> tuple[str, list[str]]:
    """The words this process was started with: the torque command when run
    through `torque`, else the jsc command line."""
    try:
        from torque import cli
        if cli.INVOCATION is not None:
            return cli.INVOCATION
    except ImportError:
        pass
    import sys as _sys
    return "jsc", list(_sys.argv[1:])


def _consumed_approval(workspace, client, invocation, org):
    from torque.approval import consumed_for_wrapper
    return consumed_for_wrapper(workspace, client, invocation, org, cwd=os.getcwd())


def _parent_approval(workspace, client, approval_id, org, invocation):
    from torque.approval import approved_parent
    return approved_parent(workspace, client, approval_id, org, invocation, cwd=os.getcwd())


def _release(workspace, client, invocation, org):
    from torque.approval import release_child, release_for_retry
    parent = os.environ.get(APPROVED_PARENT_ENV)
    if parent:
        return release_child(workspace, client, parent, org, invocation, cwd=os.getcwd())
    return release_for_retry(workspace, client, invocation, org, cwd=os.getcwd())


def release_after_resolution_failure(target_org: str) -> None:
    """Nothing ran because the org could not be resolved: in a connected workspace,
    return the approval the gate consumed for this command so it can be retried."""
    try:
        scope = _connected_scope()
    except IndeterminateScope:
        return
    if scope is None or scope[1] is None:
        return
    try:
        if _release(scope[0], scope[1], _invocation(), target_org):
            print("connected mode: the approval was not used and can be used again for the same command",
                  file=sys.stderr)
    except (OSError, ValueError):
        pass


def connected_approval(target_org: str, org_id_18: str, dry_run: bool = False):
    """(0, approval or None) when this run may proceed, else (EXIT_NOT_APPROVED, None).

    In a connected workspace, a Torque write route runs only when the gate has
    just consumed an approval for this exact command, and only against the org ID
    the consultant approved; an alias remapped since the grant is refused here.
    Dry runs and workspaces not in connected mode are unaffected."""
    if dry_run:
        return 0, None
    try:
        scope = _connected_scope()
    except IndeterminateScope as exc:
        print(f"error: connected mode cannot be ruled out: {exc}; nothing was run", file=sys.stderr)
        return EXIT_NOT_APPROVED, None
    if scope is None:
        return 0, None
    workspace, client = scope
    if client is None:
        print("error: connected mode: run this with --workspace PATH --client NAME", file=sys.stderr)
        return EXIT_NOT_APPROVED, None
    parent = os.environ.get(APPROVED_PARENT_ENV)
    if parent:
        record = _parent_approval(workspace, client, parent, target_org, _invocation())
    else:
        record = _consumed_approval(workspace, client, _invocation(), target_org)
    if record is None:
        print("error: connected mode: no approval was consumed for this exact command in the last "
              "two minutes; request one with torque approval request", file=sys.stderr)
        return EXIT_NOT_APPROVED, None
    if record.get("org_id_18") != org_id_18:
        print(f"error: connected mode: {target_org!r} now resolves to {org_id_18}, but the approval is for "
              f"{record.get('org_id_18')}; nothing was run", file=sys.stderr)
        return EXIT_NOT_APPROVED, None
    return 0, {**record, "_scope": (workspace, client)}


class WrapperContext:
    """Holds wrapper state across the 3 phases."""
    def __init__(
        self,
        operation_type: str,
        target_org: str,
        wrapper_command: str,
        invoking_intent: dict | None = None,
        parent_snapshot_id: str | None = None,
    ):
        self.operation_type = operation_type
        self.target_org = target_org
        self.wrapper_command = wrapper_command
        self.invoking_intent = invoking_intent
        self.parent_snapshot_id = parent_snapshot_id

        self.snapshot_id = uuid.uuid4().hex[:16]
        self.org: org_detect.OrgInfo | None = None
        self.snap_dir: Path | None = None
        self.manifest: dict | None = None
        self.lock_state: dict | None = None
        self.t_phase_start = 0.0
        self._lease_stop = threading.Event()
        self._lease_thread = None
        self._lease_error = None
        self._active_token = None
        self._lock_path = None
        self._lease_mutex = threading.Lock()

    def resolve_org(self) -> int:
        """Resolve target org. Returns 0 on success, error code otherwise."""
        self.org = org_detect.resolve_org(self.target_org)
        if self.org is None:
            print(f"error: cannot verify explicit org {self.target_org!r} "
                  "from org display and the Organization query",
                  file=sys.stderr)
            release_after_resolution_failure(self.target_org)
            return EXIT_TOKEN_INVALID
        rc, _ = connected_approval(self.target_org, self.org.org_id_18,
                                   dry_run="--dry-run" in str(self.wrapper_command).split())
        return rc

    def acquire_org_lock(self) -> int:
        """Try to acquire per-org lock. Returns 0 or EXIT_CONCURRENCY_CONFLICT."""
        try:
            self.lock_state = org_sequence.acquire_lock(
                self.org.org_id_short, self.org.alias,
                self.snapshot_id, self.operation_type,
                session_id=os.environ.get("CLAUDE_SESSION_ID"),
            )
        except org_sequence.LockConflictError as e:
            print(f"error: org lock unavailable: {e}", file=sys.stderr)
            return EXIT_CONCURRENCY_CONFLICT
        self._lock_path = bundle.org_dir(self.org.org_id_short, self.org.alias) / ".org_lock.json"
        self._active_token = _ACTIVE_WRAPPER.set(self)
        self._lease_thread = threading.Thread(target=self._refresh_lease, name="torque-org-lease", daemon=True)
        self._lease_thread.start()
        return 0

    def _heartbeat_once(self):
        with self._lease_mutex:
            org_sequence.heartbeat(self.org.org_id_short, self.org.alias,
                                   self.lock_state["owner_token"], lock_path=self._lock_path)

    def _refresh_lease(self):
        while not self._lease_stop.wait(org_sequence.LOCK_HEARTBEAT_SECONDS):
            try:
                self._heartbeat_once()
            except Exception as exc:
                self._lease_error = f"{type(exc).__name__}: {exc}"
                return

    def ensure_ownership(self):
        if self._lease_error is None:
            try:
                self._heartbeat_once()
            except Exception as exc:
                self._lease_error = f"{type(exc).__name__}: {exc}"
        if self._lease_error is not None:
            if self.manifest is not None:
                self.manifest["snapshot_status"] = "partial"
                self.manifest["concurrency_error"] = self._lease_error
                self.save()
            raise org_sequence.LockOwnershipError(
                "Org lease ownership could not be maintained. Earlier commands may have executed; "
                "inspect this snapshot and the explicit target before retrying. " + self._lease_error)

    def init_snapshot_dir(self):
        self.snap_dir, _ = bundle.new_snapshot_dir(
            self.org.org_id_short, self.org.alias, self.snapshot_id
        )
        # Build envelope
        operator = os.environ.get("USER", "unknown")
        self.manifest = mf.build_envelope(
            snapshot_id=self.snapshot_id,
            operation_type=self.operation_type,
            wrapper_command=self.wrapper_command,
            org_info=self.org,
            operator=operator,
            session_id=os.environ.get("CLAUDE_SESSION_ID"),
            parent_snapshot_id=self.parent_snapshot_id,
            invoking_intent=self.invoking_intent,
            sf_cli_version=mf.get_sf_cli_version(),
        )
        self.manifest["org"]["org_sequence"] = self.lock_state.get("org_sequence") if self.lock_state else None
        self.manifest["lock_state"] = self.lock_state

    def save(self):
        mf.save(self.snap_dir, self.manifest)

    def update_phase(self, phase: str, **fields):
        self.manifest["phases"][phase].update(fields)

    def set_revert_capabilities(self):
        rc = revert_capabilities.compute_revertibility(self.operation_type, self.manifest["payload"])
        self.manifest["revert_capabilities"] = rc

    def release_lock(self):
        self._lease_stop.set()
        if self._lease_thread is not None:
            self._lease_thread.join()
        if self._active_token is not None:
            _ACTIVE_WRAPPER.reset(self._active_token)
            self._active_token = None
        if self.lock_state and self.org:
            try:
                org_sequence.release(self.org.org_id_short, self.org.alias, self.lock_state["owner_token"], lock_path=self._lock_path)
            except Exception:
                pass
        if self._lease_error is not None:
            self.ensure_ownership()


def _stdout_to_str(x) -> str:
    """Normalize subprocess stdout: handles str | bytes | None.

    Per audit codex-R3-P1-21 + gemini-R4: text=True means TimeoutExpired.stdout
    is already str (not bytes), so e.stdout.decode() raises AttributeError on
    timeout.
    """
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    try:
        return x.decode(errors="replace")
    except Exception:
        return ""


def run_sf_subprocess(cmd: list[str], timeout_seconds: int = 600,
                      cwd: str | None = None) -> tuple[int, str, str]:
    """Invoke sf CLI subprocess. Returns (exit_code, stdout, stderr).

    `cwd` exists because several `sf project *` commands resolve the SFDX project
    from the working directory and hard-fail outside one. See
    stage_source_project() — a revert deploys from the snapshot store, which is
    never a project.
    """
    active = _ACTIVE_WRAPPER.get()
    if active is not None:
        active.ensure_ownership()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout_seconds, cwd=cwd)
        if active is not None:
            try:
                active.ensure_ownership()
            except org_sequence.LockOwnershipError:
                if active.snap_dir is not None:
                    bundle.atomic_write_json(active.snap_dir / "lease-lost-result.json",
                                             {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}, mode=0o600)
                raise
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired as e:
        return -1, _stdout_to_str(e.stdout), _stdout_to_str(e.stderr) + f"\nTIMEOUT after {timeout_seconds}s"
    except FileNotFoundError:
        return -2, "", "sf CLI not found in PATH"


def in_sfdx_project(start: str | None = None) -> bool:
    """True when `start` (default cwd) sits inside an SFDX project."""
    p = pathlib.Path(start or os.getcwd()).resolve()
    for d in (p, *p.parents):
        if (d / "sfdx-project.json").is_file():
            return True
    return False


def stage_source_project(source_dirs: list[str]) -> tuple[str, list[str]]:
    """Copy source dirs or exact captured files into a throwaway SFDX project.

    `sf project deploy start` resolves the project from CWD and refuses to run
    outside one:

        InvalidProjectWorkspaceError: <cwd> does not contain a valid Salesforce
        DX project.

    A REVERT deploys from `<selected-client>/state/revert/<...>/metadata-before`,
    and the operator's cwd at that moment is wherever they happened to be —
    typically the repo root, which is not an SFDX project. So the revert failed
    here every time, AFTER the executor had printed "Executing revert:" and
    after drift had been checked. Observed on sf-deploy-test 2026-07-29: the
    before-state was captured correctly, the plan was correct, and the org was
    never restored.

    The caller is responsible for cleaning up the returned directory.
    """
    stage = pathlib.Path(tempfile.mkdtemp(prefix="jsc-deploy-stage-")).resolve()
    (stage / "sfdx-project.json").write_text(json.dumps(
        {"packageDirectories": [{"path": "force-app", "default": True}],
         "namespace": ""}), encoding="utf-8")
    pkg = stage / "force-app"
    pkg.mkdir(parents=True, exist_ok=True)   # must EXIST, not just be declared
    try:
        for sd in source_dirs:
            src = pathlib.Path(sd)
            if src.is_file():
                # A field-only restore must keep its objects/<name>/fields path
                # without importing the parent object or neighboring fields.
                dest = pkg / captured_source_relative(src)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                continue
            if not src.is_dir():
                shutil.rmtree(stage, ignore_errors=True)
                raise ValueError(f"Source selector does not exist: {src}")
            for item in src.iterdir():
                if item.name.startswith("."):
                    continue          # .retrieve-result.json is forensics, not source
                dest = pkg / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return str(stage), ["force-app"]


def csv_line_ending(path: Path) -> str:
    """Match the input header's record terminator without rewriting user data.

    Salesforce CSV headers are API field names and cannot contain embedded line
    breaks. Quoted multiline values in subsequent rows are left byte-for-byte intact.
    """
    with path.open("rb") as stream:
        header = stream.readline(1024 * 1024)
    return "CRLF" if header.endswith(b"\r\n") else "LF"


def parse_sf_json_safely(stdout: str) -> dict | None:
    """Parse sf --json output. Returns None on parse failure."""
    if not stdout.strip():
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


def _outcome_exit(status: str) -> int:
    return {"complete": EXIT_SUCCESS, "applied_partial": EXIT_SUCCEEDED_PARTIAL,
            "pending_finalize_required": EXIT_IN_PROGRESS, "failed": EXIT_UNDERLYING_FAILED,
            "abandoned": EXIT_UNDERLYING_FAILED}.get(status, EXIT_POST_FINALIZE_FAILED)


def classify_deploy_status(sf_json, exit_code: int) -> tuple[str, int]:
    from ..job_outcomes import deploy_outcome
    status, _ = deploy_outcome(sf_json, exit_code)
    return status, _outcome_exit(status)


def classify_bulk_status(sf_json, exit_code: int, *, expected_job_id=None) -> tuple[str, int, str | None]:
    from ..job_outcomes import bulk_outcome
    status, job_id = bulk_outcome(sf_json, exit_code, expected_job_id)
    return status, _outcome_exit(status), job_id


def capture_bulk_outcome(ctx, sf_json, exit_code: int) -> tuple[str, int, str | None]:
    """Observe a known job when the CLI response lacks conclusive row counts.

    This is an exact-job read, never a mutation retry. CLI result CSVs and raw
    output stay in this private snapshot, including on partial application.
    """
    outcome = classify_bulk_status(sf_json, exit_code)
    status, _, job_id = outcome
    if not job_id or status not in ("partial", "pending_finalize_required"):
        return outcome
    command = ["sf", "data", "bulk", "results", "--target-org", ctx.org.alias,
               "--job-id", job_id, "--json"]
    code, stdout, stderr = run_sf_subprocess(command, timeout_seconds=120, cwd=str(ctx.snap_dir))
    path = ctx.snap_dir / "bulk-result-observation.json"
    bundle.atomic_write_json(path, {"command": command, "exit_code": code,
                                    "stdout": stdout, "stderr": stderr})
    ctx.manifest["payload"]["result_observation"] = str(path)
    observed = classify_bulk_status(parse_sf_json_safely(stdout), code, expected_job_id=job_id)
    if observed[0] in ("complete", "applied_partial", "failed"):
        return observed
    # A failed upload may have no result CSV. Read the same job's REST status;
    # do not execute the CLI's suggested prose or use any target from that prose.
    command = ["sf", "api", "request", "rest", f"/services/data/v62.0/jobs/ingest/{job_id}",
               "--method", "GET", "--target-org", ctx.org.alias, "--json"]
    code, stdout, stderr = run_sf_subprocess(command, timeout_seconds=120, cwd=str(ctx.snap_dir))
    path = ctx.snap_dir / "bulk-job-observation.json"
    parsed = parse_sf_json_safely(stdout)
    # Cookie-bearing response headers are unnecessary; persist exact job body and
    # response status, retaining malformed text only when no JSON is available.
    result = parsed.get("result") if isinstance(parsed, dict) else None
    if isinstance(result, dict) and "headers" in result:
        parsed = {**parsed, "result": {key: value for key, value in result.items() if key != "headers"}}
        stdout = json.dumps(parsed)
    bundle.atomic_write_json(path, {"command": command, "exit_code": code, "stdout": stdout, "stderr": stderr})
    ctx.manifest["payload"]["job_observation"] = str(path)
    final = classify_bulk_status(parsed, code, expected_job_id=job_id)
    # A transient read failure cannot erase the original submitted job identity.
    return final if final[0] in ("complete", "applied_partial", "failed") else outcome


def auto_enqueue_if_pending(ctx, snap_status: str, job_id: str | None,
                            sf_report_command: list[str]) -> str | None:
    """Codex-v7.17-P1-02 closure: when underlying returns pending_finalize_required
    AND we have a job_id, enqueue to post_deploy_polling so `jsc post-deploy daemon`
    can finalize.

    Returns the queue_entry_id if enqueued, None otherwise.
    """
    if snap_status != "pending_finalize_required" or not job_id:
        return None
    try:
        from .. import post_deploy_polling
        queue_entry_id = post_deploy_polling.enqueue(
            snapshot_id=ctx.snapshot_id,
            org_id_short=ctx.org.org_id_short,
            alias=ctx.org.alias,
            operation_type=ctx.operation_type,
            job_id=job_id,
            sf_report_command=sf_report_command,
        )
        ctx.manifest.setdefault("payload", {})
        ctx.manifest["payload"]["polling_queue_entry_id"] = queue_entry_id
        return queue_entry_id
    except Exception as e:
        # Best-effort — never block wrapper on enqueue failure
        print(f"warning: auto-enqueue to polling queue failed: {e}", file=sys.stderr)
        return None


def assert_lock_safe_or_opt_in(timeout_seconds: int) -> int:
    """Compatibility helper: maintained leases support ordinary long operations."""
    if timeout_seconds <= 0:
        raise ValueError("operation timeout must be positive")
    return 0


import sys  # for context.error printing
