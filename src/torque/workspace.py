"""Local workspace records. No credentials, org calls, global settings, or inferred QA."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
from importlib import resources
import json
import os
from pathlib import Path
import re
import shlex
import stat
import tempfile
from datetime import datetime, timezone
from uuid import uuid4

PROFILES = ("generic", "solution-lead")
STATUSES = ("prepared", "executed", "verified", "incomplete")
CONFIG = "workspace.json"
_SESSION_ID = re.compile(r"[0-9]{8}T[0-9]{12}Z-[a-f0-9]{12}\Z")


class WorkspaceError(ValueError):
    """An actionable local configuration or input error."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def slug_for(name: str) -> str:
    name = name.strip()
    if not name or "/" in name or "\\" in name or ".." in name:
        raise WorkspaceError("client name must be nonempty and cannot contain paths or '..'")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug or len(slug) > 80:
        raise WorkspaceError("client name must produce a slug of 1–80 letters, digits or hyphens")
    return slug


def _inside(root: Path, path: Path) -> Path:
    """Keep client-owned paths within their root; do not follow swapped symlinks."""
    root = root.resolve()
    try:
        parts = path.relative_to(root).parts
    except ValueError as exc:
        raise WorkspaceError("path is outside the selected workspace") from exc
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise WorkspaceError(f"workspace path must not be a symlink: {current}")
    return path


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WorkspaceError(f"cannot read valid local configuration: {path}") from exc
    if not isinstance(value, dict):
        raise WorkspaceError(f"expected a JSON object: {path}")
    return value


def atomic_write_new(path: Path, text: str) -> None:
    """Publish one complete private file atomically, without replacing an existing file."""
    if not path.parent.is_dir():
        raise WorkspaceError(f"output directory does not exist: {path.parent}")
    fd, temporary = tempfile.mkstemp(prefix=".torque-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise WorkspaceError(f"file already exists; choose a new path: {path}") from exc
    finally:
        Path(temporary).unlink(missing_ok=True)


def _atomic_replace_text(path: Path, text: str) -> None:
    """Replace an explicitly managed private file without exposing a partial write."""
    fd, temporary = tempfile.mkstemp(prefix=".torque-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _write_json(path: Path, value: dict) -> None:
    atomic_write_new(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def _torque_project(path: Path) -> bool:
    """Recognize this project's explicit identity, including on Python 3.10."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    try:
        import tomllib
    except ModuleNotFoundError:
        # The shipped pyproject uses a simple [project].name assignment. On 3.10
        # avoid treating text inside a multiline TOML value as a declaration.
        if '\"\"\"' in text or "'''" in text:
            return False
        section = None
        for raw in text.splitlines():
            line = raw.strip()
            header = re.fullmatch(r"\[([^\[\]]+)\]\s*(?:#.*)?", line)
            if header:
                section = header[1].strip()
            elif section == "project":
                name = re.fullmatch(r'''name\s*=\s*(["'])([A-Za-z0-9_.-]+)\1\s*(?:#.*)?''', line)
                if name:
                    return re.sub(r"[-_.]+", "-", name[2]).lower() == "torque-salesforce"
        return False
    try:
        project = tomllib.loads(text).get("project", {})
        name = project.get("name") if isinstance(project, dict) else None
        return isinstance(name, str) and re.sub(r"[-_.]+", "-", name).lower() == "torque-salesforce"
    except ValueError:
        return False


def _checkout_containing(path: Path) -> Path | None:
    for ancestor in (path, *path.parents):
        if ((ancestor / "src/torque/__init__.py").is_file()
                and (ancestor / "src/torque/workspace.py").is_file()
                and _torque_project(ancestor / "pyproject.toml")):
            return ancestor
    return None


def _source_checkout() -> Path | None:
    return _checkout_containing(Path(__file__).resolve())


def _materialize_workflows(root: Path) -> None:
    """Install packaged conversation files in this workspace, preserving existing local files."""
    try:
        data = resources.files("torque").joinpath("data")
    except (ModuleNotFoundError, TypeError):
        return

    def copy_tree(source, destination: Path) -> None:
        if not source.is_dir():
            return
        _inside(root, destination).mkdir(parents=True, exist_ok=True, mode=0o700)
        for item in source.iterdir():
            target = _inside(root, destination / item.name)
            if item.is_dir():
                copy_tree(item, target)
            elif item.is_file() and item.name.endswith(".md") and not target.exists():
                atomic_write_new(target, item.read_text(encoding="utf-8"))

    for group in ("commands", "rules", "skills", "agents"):
        copy_tree(data.joinpath(group), root / ".claude" / group)
    copy_tree(data.joinpath("skills"), root / ".agents" / "skills")


def init_workspace(path: str | Path, name: str, profile: str = "generic") -> Path:
    if not name.strip():
        raise WorkspaceError("workspace name must be nonempty")
    if profile not in PROFILES:
        raise WorkspaceError(f"unknown profile: {profile}")
    target = Path(path).expanduser()
    if target.is_symlink():
        raise WorkspaceError("workspace root must not be a symlink")
    root = target.resolve()
    source = _source_checkout()
    if source is not None:
        source = source.resolve()
    if (source and (root == source or source in root.parents)) or _checkout_containing(root):
        raise WorkspaceError("choose a private workspace outside the Torque source checkout")
    if (root / CONFIG).exists():
        raise WorkspaceError(f"workspace already initialized: {root}")
    if root.exists() and not root.is_dir():
        raise WorkspaceError(f"workspace path is not a directory: {root}")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    clients = _inside(root, root / "clients")
    clients.mkdir(mode=0o700, exist_ok=True)
    firm = {"schema": "torque.workspace/1", "name": name.strip(),
            "profile": profile, "created_at": _now()}
    focus_notes = ""
    if profile == "solution-lead":
        firm["role"] = "Salesforce Solution Lead"
        firm["delivery_focus"] = ["discovery", "business analysis", "solution design and configuration",
                                  "QA and UAT", "support", "training", "small project coordination"]
        focus_notes = (
            "## Delivery focus\n\nRole: Salesforce Solution Lead.\n\n"
            "Use this workspace for discovery and business analysis; solution design and "
            "Salesforce configuration; QA and UAT; support and diagnosis; client training; "
            "and coordination of small projects. Keep requirements, decisions, configuration "
            "changes, validation evidence and client-facing handoffs connected within each client.\n\n"
            "For discovery, capture the process, stakeholders, current pain points and acceptance "
            "criteria. For delivery, retain the proposed approach, actual changes and outstanding "
            "questions. For support, record reproducible symptoms and observed results. For "
            "training, preserve the intended audience and practical task steps. For coordination, "
            "record owners, agreed next actions and dependencies without inventing commitments.\n\n"
            "These are editable working defaults, not statements of the firm's approved policies.\n\n"
        )
    ignore = _inside(root, root / ".gitignore")
    if not ignore.exists():
        atomic_write_new(ignore, "# Private client data and artifacts\n*\n")
    else:
        original = ignore.read_text(encoding="utf-8")
        private_rules = ["/clients/", "/workspace.json", "/profile.md", "/.torque/"]
        if any(rule not in original.splitlines() for rule in private_rules):
            updated = original.rstrip("\n") + "\n\n# Torque private workspace\n" + "\n".join(private_rules) + "\n"
            _atomic_replace_text(ignore, updated)
    agent_notes = (
        "# Private Torque workspace\n\n"
        "This directory holds private employer and client work. Begin by reading profile.md. "
        "Select the client the user named; never infer another client or org. From this directory, "
        "use `torque context --workspace . --client NAME` to resume that client's notes and journal.\n\n"
        "Discover the working recipes with `torque workflows`, then read one with "
        "`torque workflows show NAME --workspace .`. An explicitly selected workspace uses its "
        "local .claude/commands/NAME.md when present, then the packaged recipe as fallback. "
        "Without a selected workspace, show reads the packaged reference. These recipes support conversational work; "
        "use the user's actual authorization and existing Salesforce access for operations. "
        "No Torque hooks, approval tokens or global sf replacement are required.\n\n"
        "Keep client files in clients/SLUG/. Use --workspace . --client SLUG when calling "
        "stateful native workflows. Keep credentials out of notes and session summaries. "
        "Record progress with `torque session add --workspace . --client NAME --summary TEXT "
        "--status prepared|executed|verified|incomplete`. Status is user-reported; distinguish "
        "actual evidence, assertions and unanswered questions. Use `torque handoff` to render "
        "the recorded history. Nothing here implies organizational approval or completed QA.\n"
    )
    for filename, content in {
        "AGENTS.md": agent_notes,
        "CLAUDE.md": "# Torque workspace\n\nRead AGENTS.md in this directory for the shared workflow, "
                     "then use `torque context --workspace . --client NAME` for the selected client.\n",
        "profile.md": f"# {name.strip()}\n\nProfile: {profile}\n\n"
                      + focus_notes + "Record the firm's actual delivery conventions, approved tools, artifact locations "
                      "and relevant policies here. This starter does not assume they are already agreed.\n",
    }.items():
        if not (root / filename).exists():
            atomic_write_new(root / filename, content)
    _materialize_workflows(root)
    # Publish the configuration last: an interrupted first-run can resume safely.
    _write_json(root / CONFIG, firm)
    from .template_updates import record_initial_templates
    record_initial_templates(root)
    return root


def load_workspace(path: str | Path) -> tuple[Path, dict]:
    root = Path(path).expanduser().resolve()
    config = _read_json(_inside(root, root / CONFIG))
    if (config.get("schema") != "torque.workspace/1" or not isinstance(config.get("name"), str)
            or not config["name"].strip() or config.get("profile") not in PROFILES):
        raise WorkspaceError(f"invalid workspace configuration: {root / CONFIG}")
    _inside(root, root / "clients")
    return root, config


@contextmanager
def _client_creation_lock(root: Path):
    """Serialize complete client publication; an interrupted attempt holds no slug."""
    import fcntl
    private = _inside(root, root / ".torque")
    private.mkdir(mode=0o700, exist_ok=True)
    path = _inside(root, private / "client-creation.lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise WorkspaceError("client creation lock must be a regular file")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def add_client(workspace: str | Path, name: str, org: str | None = None) -> Path:
    root, _ = load_workspace(workspace)
    slug = slug_for(name)
    if org is not None and (not org.strip() or any(c in org for c in "\r\n\0")):
        raise WorkspaceError("org alias must be nonempty and on one line")
    with _client_creation_lock(root):
        client = _inside(root, root / "clients" / slug)
        if client.exists():
            raise WorkspaceError(f"client slug already exists: {slug}; no existing data was replaced")
        staging = _inside(root, root / ".torque" / "client-staging")
        staging.mkdir(mode=0o700, exist_ok=True)
        # A killed process can leave private staging behind, but not an occupied
        # client slug. A caught failure removes only this attempt's directory.
        with tempfile.TemporaryDirectory(prefix=slug + "-", dir=staging) as temporary:
            pending = Path(temporary)
            for directory in ("sessions", "artifacts", "context", "config"):
                (pending / directory).mkdir(mode=0o700)
            _write_json(pending / "client.json", {"schema": "torque.client/1", "name": name.strip(),
                                                  "slug": slug, "org": org.strip() if org else None,
                                                  "created_at": _now()})
            atomic_write_new(pending / "context.md", f"# {name.strip()}\n\n"
                             "Record this client's current scope, decisions, contacts, constraints and "
                             "open questions here. Keep credentials out of these notes.\n")
            _inside(root, client)
            if client.exists():
                raise WorkspaceError(f"client slug already exists: {slug}; no existing data was replaced")
            pending.rename(client)
    return client


def list_clients(workspace: str | Path) -> list[dict]:
    root, _ = load_workspace(workspace)
    directory = _inside(root, root / "clients")
    if not directory.is_dir():
        return []
    clients = []
    for child in sorted(directory.iterdir()):
        _inside(root, child)
        if child.is_dir() and (child / "client.json").exists():
            clients.append(load_client(root, child.name)[2])
    return clients


def load_client(workspace: str | Path, name: str) -> tuple[Path, dict, dict]:
    root, firm = load_workspace(workspace)
    slug = slug_for(name)
    client = _inside(root, root / "clients" / slug)
    config = _read_json(_inside(root, client / "client.json"))
    if config.get("schema") != "torque.client/1" or config.get("slug") != slug:
        raise WorkspaceError(f"invalid client configuration: {client / 'client.json'}")
    if not isinstance(config.get("name"), str) or not config["name"].strip():
        raise WorkspaceError("client configuration has no name")
    return client, firm, config


def add_session(workspace: str | Path, client_name: str, summary: str,
                status: str = "prepared", evidence: str | Path | None = None) -> dict:
    client, _, config = load_client(workspace, client_name)
    if not summary.strip():
        raise WorkspaceError("session summary must be nonempty")
    if status not in STATUSES:
        raise WorkspaceError(f"unknown session status: {status}")
    evidence_ref = None
    if evidence is not None:
        path = Path(evidence).expanduser().resolve()
        if not path.is_file():
            raise WorkspaceError(f"evidence is not a readable file: {path}")
        clients_dir = client.parent
        if clients_dir in path.parents and client not in path.parents:
            raise WorkspaceError("evidence belongs to a different client")
        try:
            digest = _file_hash(path)
        except OSError as exc:
            raise WorkspaceError(f"cannot read evidence: {path}") from exc
        evidence_ref = {"path": str(path), "sha256": digest,
                        "basis": "file reference recorded; contents not evaluated"}
    at = _now()
    entry_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:12]
    entry = {"schema": "torque.session/1", "id": entry_id, "created_at": at,
             "client": config["slug"], "summary": summary.strip(), "status": status,
             "status_basis": "user_reported", "independently_verified": False,
             "evidence": evidence_ref}
    sessions = _inside(client, client / "sessions")
    sessions.mkdir(mode=0o700, exist_ok=True)
    _write_json(_inside(client, sessions / f"{entry_id}.json"), entry)
    return entry


def _file_hash(path: Path) -> str:
    """Hash large local artifacts without loading the entire file into memory."""
    digest = hashlib.sha256()
    # O_NONBLOCK prevents a replaced named pipe from hanging resumption.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("evidence is not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    finally:
        os.close(fd)
    return digest.hexdigest()


def _session_evidence(client: Path, evidence: object) -> str:
    if evidence is None:
        return "not_supplied"
    if (not isinstance(evidence, dict) or not isinstance(evidence.get("path"), str)
            or not evidence["path"] or "\0" in evidence["path"]
            or not Path(evidence["path"]).is_absolute()
            or not isinstance(evidence.get("sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", evidence["sha256"])
            or evidence.get("basis") != "file reference recorded; contents not evaluated"):
        raise WorkspaceError("invalid session evidence reference")
    path = Path(evidence["path"])
    try:
        resolved = path.resolve()
        if client.parent in resolved.parents and client not in resolved.parents:
            raise WorkspaceError("session evidence belongs to a different client")
        if path != resolved:
            return "unavailable"  # A saved canonical reference now traverses a symlink.
        return "matches_reference" if _file_hash(path) == evidence["sha256"] else "changed"
    except FileNotFoundError:
        return "missing"
    except (OSError, RuntimeError):
        return "unavailable"


def _session_timestamp(value: object) -> bool:
    try:
        return isinstance(value, str) and datetime.fromisoformat(value).utcoffset() is not None
    except ValueError:
        return False


def list_sessions(workspace: str | Path, client_name: str, limit: int | None = 20) -> list[dict]:
    client, _, config = load_client(workspace, client_name)
    sessions = _inside(client, client / "sessions")
    if not sessions.exists():
        return []
    paths = sorted(sessions.glob("*.json"), reverse=True)
    if limit is not None:
        paths = paths[:limit]
    entries = []
    for path in paths:
        value = _read_json(_inside(client, path))
        if (not _SESSION_ID.fullmatch(path.stem)
                or value.get("schema") != "torque.session/1" or value.get("client") != config["slug"]
                or value.get("id") != path.stem or value.get("status") not in STATUSES
                or value.get("status_basis") != "user_reported"
                or value.get("independently_verified") is not False
                or not isinstance(value.get("summary"), str)
                or not value["summary"].strip() or not _session_timestamp(value.get("created_at"))):
            raise WorkspaceError(f"invalid session record: {path}")
        try:
            value["evidence_integrity"] = _session_evidence(client, value.get("evidence"))
        except WorkspaceError as exc:
            raise WorkspaceError(f"{exc}: {path}") from exc
        entries.append(value)
    return entries


def _change_context(workspace: str | Path, client_name: str, client: Path) -> list[dict]:
    """Bound event summaries without converting reported checks into observed acceptance."""
    from .changes import get_change, list_changes
    summaries = []
    limit = 5
    for record in list_changes(workspace, client_name):
        detail = get_change(workspace, client_name, record["id"])
        groups = {"decisions": "decision", "metadata_observations": "metadata_observation",
                  "next_steps": "next_step"}
        histories, totals = {}, {}
        for name, kind in groups.items():
            events = [event for event in detail["events"] if event["kind"] == kind]
            totals[name] = len(events)
            histories[name] = []
            for event in events[-limit:]:
                row = {key: event[key] for key in ("id", "created_at", "summary", "basis")}
                if kind == "metadata_observation":
                    row.update({key: event.get(key) for key in
                                ("target_org", "job_id", "result", "evidence_integrity", "manifest_integrity")})
                    row["business_acceptance_proven"] = False
                histories[name].append(row)
        truncated = {name: total > limit for name, total in totals.items()}
        summaries.append({**record, "criteria": detail["criteria"], "assessment": detail["assessment"],
                          **histories, "history_counts": totals, "history_limit_per_kind": limit,
                          "history_truncated": truncated,
                          "history_note": ("Only the latest five entries per history kind are shown; use show_command for full history."
                                           if any(truncated.values()) else "All decision, metadata and next-step history is shown."),
                          "show_command": shlex.join(["torque", "change", "show", record["id"],
                                                      "--workspace", str(client.parent.parent), "--client", client.name])})
    return summaries


def client_output_path(workspace: str | Path, client_name: str, destination: str | Path) -> Path:
    """Allow an explicit export, but never place this client's data under a sibling client."""
    client, _, _ = load_client(workspace, client_name)
    output = Path(destination).expanduser().absolute()
    resolved = output.resolve()
    if client.parent in resolved.parents and client not in resolved.parents:
        raise WorkspaceError("handoff output belongs to a different client; choose this client's directory or an explicit export outside clients/")
    return output


def _context_notes(client: Path) -> dict[str, str]:
    """Read the same bounded, selected notes for resumption and handoff."""
    notes = {}
    candidates = [("Firm notes", client.parent.parent / "profile.md", client.parent.parent),
                  ("Client notes", client / "context.md", client)]
    context_dir = _inside(client, client / "context")
    if context_dir.is_dir():
        candidates.extend((f"Client context: {p.name}", p, client)
                          for p in sorted(context_dir.glob("*.md")))
    for title, path, owner in candidates:
        _inside(owner, path)
        if path.is_file():
            with path.open(encoding="utf-8") as stream:
                text = stream.read(65537)
            notes[title] = text[:65536] + ("\n[truncated at 65,536 characters]" if len(text) > 65536 else "")
    return notes


def get_context(workspace: str | Path, client_name: str) -> dict:
    client, firm, config = load_client(workspace, client_name)
    notes = _context_notes(client)
    return {"workspace": firm, "client": config, "client_root": str(client),
            "changes": _change_context(workspace, client_name, client),
            "notes": notes,
            "sessions": list_sessions(workspace, client_name),
            "evidence_note": "Journal statuses are user-reported, not independent verification."}


def render_handoff(workspace: str | Path, client_name: str) -> str:
    client_root, firm, client = load_client(workspace, client_name)
    notes = _context_notes(client_root)
    entries = list_sessions(workspace, client_name, limit=None)
    lines = [f"# Handoff: {client['name']}", "", f"Workspace: {firm['name']}",
             f"Profile: {firm['profile']}", f"Configured org: {client.get('org') or 'not specified'}",
             "", "Statuses below were supplied by the user. This journal did not independently "
             "verify execution or outcomes.", ""]
    if notes:
        lines += ["## Working context", ""]
        for title, contents in notes.items():
            lines += [f"### {title}", "", contents.rstrip(), ""]
    if not entries:
        lines.append("No session entries have been recorded for this client.")
    for entry in reversed(entries):
        lines += [f"## {entry['created_at']} — {entry['status']} (user-reported)", "",
                  entry["summary"], "", f"Entry: {entry['id']}"]
        evidence = entry.get("evidence")
        if evidence:
            lines += [f"Evidence reference: {evidence['path']}", f"Recorded SHA-256: {evidence['sha256']}",
                      f"Evidence integrity: {entry['evidence_integrity'].replace('_', ' ')}",
                      "The reference was hashed when recorded; its content was not assessed."]
        else:
            lines.append("Evidence reference: none supplied.")
        lines.append("")
    from .changes import list_changes, render_change
    for change in list_changes(workspace, client_name):
        lines += ["", "---", "", render_change(workspace, client_name, change["id"]).rstrip()]
    return "\n".join(lines).rstrip() + "\n"
