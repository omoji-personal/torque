"""An independent before-state for a production approval: captured by its own
recorded step before the request, never the snapshot a wrapper takes inside the
approved write. Stored in the change's evidence with a hash per file."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from uuid import uuid4

from . import changes, workspace as ws

# Where a component appears in a retrieve, in source format and in metadata
# (--target-metadata-dir) format. A child (field, rule) is covered by its own file
# or by its parent object's file.
TYPE_NEEDLES = {
    "Flow": ("flows/{name}.flow",),
    "ApexClass": ("classes/{name}.cls",),
    "ApexTrigger": ("triggers/{name}.trigger",),
    "ApexPage": ("pages/{name}.page",),
    "ApexComponent": ("components/{name}.component",),
    "Layout": ("layouts/{name}.layout",),
    "FlexiPage": ("flexipages/{name}.flexipage",),
    "PermissionSet": ("permissionsets/{name}.permissionset",),
    "CustomObject": ("objects/{name}/", "objects/{name}.object"),
    "LightningComponentBundle": ("lwc/{name}/",),
    "AuraDefinitionBundle": ("aura/{name}/",),
    "CustomField": ("objects/{parent}/fields/{child}.field", "objects/{parent}.object"),
    "ValidationRule": ("objects/{parent}/validationRules/{child}.validationRule", "objects/{parent}.object"),
    "RecordType": ("objects/{parent}/recordTypes/{child}.recordType", "objects/{parent}.object"),
    "ListView": ("objects/{parent}/listViews/{child}.listView", "objects/{parent}.object"),
    "Record": ("records/{record}",),
}
METADATA_FLAGS = ("-m", "--metadata")
MANIFEST_FLAGS = ("-x", "--manifest")
MAX_MANIFEST_BYTES = 2 * 1024 * 1024


def _digest(files: list[dict]) -> str:
    text = "".join(f"{f['path']}\0{f['sha256']}\n" for f in sorted(files, key=lambda f: f["path"]))
    return hashlib.sha256(text.encode()).hexdigest()


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _store(workspace, client, change_id, source_dir: Path, job: str | None, how: str) -> dict:
    root, record = changes.load_change(workspace, client, change_id)
    evidence = ws._inside(root, root / "evidence")
    evidence.mkdir(mode=0o700, exist_ok=True)
    target = ws._inside(root, evidence / ("before-" + uuid4().hex))
    shutil.copytree(source_dir, target, symlinks=True)
    files = []
    for path in sorted(target.rglob("*")):
        if path.is_symlink():
            shutil.rmtree(target)
            raise ws.WorkspaceError(f"a before-state must not contain links: {path.relative_to(target)}")
        if path.is_file():
            if os.name != "nt":
                path.chmod(0o600)
            files.append({"path": path.relative_to(root).as_posix(), "sha256": _file_sha(path)})
    if not files:
        shutil.rmtree(target)
        raise ws.WorkspaceError("the before-state has no files")
    event = changes._append(root, record, {"kind": changes.BEFORE_STATE_KIND, "summary": f"before-state by {how}",
                                           "basis": "torque_before_state",
                                           "path": target.relative_to(root).as_posix(),
                                           "files": files, "sha256": _digest(files), "job": job})
    return _summary(event)


def _summary(event: dict) -> dict:
    return {"event_id": event["id"], "path": event["path"], "sha256": event["sha256"], "files": event["files"],
            "captured_at": event["created_at"], "job": event.get("job")}


def import_before_state(workspace, client, change_id, source) -> dict:
    """Copy an earlier retrieve or record export (a directory) into the change's evidence."""
    source = Path(source).expanduser().absolute()
    if source.is_symlink() or not source.is_dir():
        raise ws.WorkspaceError(f"before-state must be a directory (not a link): {source}")
    folder, _, _ = ws.load_client(workspace, client)
    clients = folder.parent
    resolved = source.resolve()
    if (clients == resolved or clients in resolved.parents) and not (folder == resolved or folder in resolved.parents):
        raise ws.WorkspaceError("the before-state belongs to a different client")
    return _store(workspace, client, change_id, resolved, None, "import")


def _sf_json(run, cmd: list[str], timeout: int) -> dict:
    try:
        done = run(cmd, capture_output=True, text=True, timeout=timeout)
        data = json.loads(done.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ws.WorkspaceError(f"{' '.join(cmd[:4])} failed; no before-state recorded ({exc})") from exc
    if done.returncode != 0 or not isinstance(data, dict) or data.get("status") != 0:
        message = str(data.get("message") if isinstance(data, dict) else "") or (done.stderr or "")
        raise ws.WorkspaceError(f"{' '.join(cmd[:4])} failed; no before-state recorded: {message[:300]}")
    return data


def capture_metadata(workspace, client, change_id, org, components: list[str], run=subprocess.run) -> dict:
    """Retrieve the named components now, as their own recorded step."""
    if not components:
        raise ws.WorkspaceError("name at least one component (Type:Name)")
    with tempfile.TemporaryDirectory(prefix="torque-before-") as tmp:
        cmd = ["sf", "project", "retrieve", "start", "--target-org", org, "--target-metadata-dir", tmp,
               "--unzip", "--json"]
        for component in components:
            cmd += ["--metadata", component]
        data = _sf_json(run, cmd, 600)
        result = data.get("result") if isinstance(data.get("result"), dict) else {}
        for archive in Path(tmp).glob("*.zip"):
            archive.unlink()
        return _store(workspace, client, change_id, Path(tmp), result.get("id"), "retrieve")


def _record_file(sobject: str, record_id: str) -> str:
    return f"{sobject}__{record_id}.json"


def capture_records(workspace, client, change_id, org, records: list[str], run=subprocess.run) -> dict:
    """Read each record now (Object:Id), as its own recorded step."""
    if not records:
        raise ws.WorkspaceError("name at least one record (Object:Id)")
    with tempfile.TemporaryDirectory(prefix="torque-before-") as tmp:
        folder = Path(tmp) / "records"
        folder.mkdir()
        for item in records:
            sobject, _, record_id = item.partition(":")
            if not sobject.replace("_", "").isalnum() or not record_id.isalnum():
                raise ws.WorkspaceError(f"record must be Object:Id, got {item!r}")
            data = _sf_json(run, ["sf", "data", "get", "record", "--sobject", sobject, "--record-id", record_id,
                                  "--target-org", org, "--json"], 120)
            (folder / _record_file(sobject, record_id)).write_text(json.dumps(data.get("result"), indent=2),
                                                                   encoding="utf-8")
        return _store(workspace, client, change_id, Path(tmp), None, "record read")


def _manifest_components(path: Path) -> list[str]:
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ws.WorkspaceError(f"manifest is too large to read: {path}")
        tree = ET.fromstring(path.read_bytes())
    except (OSError, ET.ParseError, ValueError) as exc:
        raise ws.WorkspaceError(f"cannot read manifest {path}") from exc
    out = []
    for types in tree.iter():
        if types.tag.split("}")[-1] != "types":
            continue
        name = next((c.text or "" for c in types if c.tag.split("}")[-1] == "name"), "")
        out += [f"{name}:{m.text or ''}" for m in types if m.tag.split("}")[-1] == "members"]
    return out


def deploy_components(argv: list[str], cwd: Path) -> list[str]:
    """Components a deploy command names with --metadata or a manifest."""
    out: list[str] = []
    for i, tok in enumerate(argv):
        name, _, attached = tok.partition("=")
        value = attached if tok.startswith("--") and attached else (argv[i + 1] if i + 1 < len(argv) else None)
        if value is None:
            continue
        if (name if tok.startswith("--") else tok) in METADATA_FLAGS:
            out.append(value)
        elif (name if tok.startswith("--") else tok) in MANIFEST_FLAGS:
            out += _manifest_components(Path(cwd) / value)
    return list(dict.fromkeys(out))


def _needles(component: str) -> list[str]:
    kind, _, name = component.partition(":")
    patterns = TYPE_NEEDLES.get(kind)
    if not name or "*" in name:
        return []
    if patterns is None:
        return [name]
    parent, _, child = name.partition(".")
    sobject, _, record_id = name.partition(":")
    return [p.format(name=name, parent=parent, child=child, record=_record_file(sobject, record_id))
            for p in patterns]


def coverage(components: list[str], before: dict) -> list[str]:
    """Components the before-state does not contain (a wildcard is never covered)."""
    paths = [f["path"] for f in before.get("files", [])]
    return [c for c in components if not any(n in p for n in _needles(c) for p in paths)]


def load_before_state(workspace, client, change_id, event_id, verify: bool = True) -> dict:
    """The recorded before-state; with verify, every file must still match its hash."""
    item = changes.get_change(workspace, client, change_id)
    event = next((e for e in item["events"] if e["id"] == event_id and e["kind"] == changes.BEFORE_STATE_KIND),
                 None)
    if event is None:
        raise ws.WorkspaceError(f"no before-state {event_id} in {change_id}")
    if verify:
        root = Path(item["change_root"])
        for entry in event["files"]:
            path = ws._inside(root, root / entry["path"])
            if not path.is_file() or _file_sha(path) != entry["sha256"]:
                raise ws.WorkspaceError(f"the before-state file {entry['path']} changed or is missing")
    return _summary(event)
