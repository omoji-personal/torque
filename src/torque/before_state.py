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

from . import argv_flags, changes, workspace as ws
import re

# The file that holds a component's content in a retrieve, in source format and in
# metadata (--target-metadata-dir) format. A path must end with one of these (a
# folder form, ending "/", must contain a file). A -meta.xml companion alone does
# not cover source such as Apex. A child (field, rule) is covered by its own file
# or by its parent object's file.
TYPE_NEEDLES = {
    "Flow": ("flows/{name}.flow-meta.xml", "flows/{name}.flow"),
    "ApexClass": ("classes/{name}.cls",),
    "ApexTrigger": ("triggers/{name}.trigger",),
    "ApexPage": ("pages/{name}.page",),
    "ApexComponent": ("components/{name}.component",),
    "Layout": ("layouts/{name}.layout-meta.xml", "layouts/{name}.layout"),
    "FlexiPage": ("flexipages/{name}.flexipage-meta.xml", "flexipages/{name}.flexipage"),
    "PermissionSet": ("permissionsets/{name}.permissionset-meta.xml", "permissionsets/{name}.permissionset"),
    "CustomObject": ("objects/{name}/", "objects/{name}.object"),
    "LightningComponentBundle": ("lwc/{name}/",),
    "AuraDefinitionBundle": ("aura/{name}/",),
    "CustomField": ("objects/{parent}/fields/{child}.field-meta.xml", "objects/{parent}.object"),
    "ValidationRule": ("objects/{parent}/validationRules/{child}.validationRule-meta.xml",
                       "objects/{parent}.object"),
    "RecordType": ("objects/{parent}/recordTypes/{child}.recordType-meta.xml", "objects/{parent}.object"),
    "ListView": ("objects/{parent}/listViews/{child}.listView-meta.xml", "objects/{parent}.object"),
    "Record": ("records/{record}",),
}
# Source-format file (relative to a package folder) -> component, for source-directory deploys.
SOURCE_PATTERNS = (
    (r"(?:^|/)classes/([^/]+)\.cls(?:-meta\.xml)?$", "ApexClass:{0}"),
    (r"(?:^|/)triggers/([^/]+)\.trigger(?:-meta\.xml)?$", "ApexTrigger:{0}"),
    (r"(?:^|/)pages/([^/]+)\.page(?:-meta\.xml)?$", "ApexPage:{0}"),
    (r"(?:^|/)components/([^/]+)\.component(?:-meta\.xml)?$", "ApexComponent:{0}"),
    (r"(?:^|/)flows/([^/]+)\.flow(?:-meta\.xml)?$", "Flow:{0}"),
    (r"(?:^|/)layouts/([^/]+)\.layout(?:-meta\.xml)?$", "Layout:{0}"),
    (r"(?:^|/)flexipages/([^/]+)\.flexipage(?:-meta\.xml)?$", "FlexiPage:{0}"),
    (r"(?:^|/)permissionsets/([^/]+)\.permissionset(?:-meta\.xml)?$", "PermissionSet:{0}"),
    (r"(?:^|/)objects/([^/]+)/fields/([^/]+)\.field-meta\.xml$", "CustomField:{0}.{1}"),
    (r"(?:^|/)objects/([^/]+)/validationRules/([^/]+)\.validationRule-meta\.xml$", "ValidationRule:{0}.{1}"),
    (r"(?:^|/)objects/([^/]+)/recordTypes/([^/]+)\.recordType-meta\.xml$", "RecordType:{0}.{1}"),
    (r"(?:^|/)objects/([^/]+)/listViews/([^/]+)\.listView-meta\.xml$", "ListView:{0}.{1}"),
    (r"(?:^|/)objects/([^/]+)/[^/]+\.object-meta\.xml$", "CustomObject:{0}"),
    (r"(?:^|/)lwc/([^/]+)/", "LightningComponentBundle:{0}"),
    (r"(?:^|/)aura/([^/]+)/", "AuraDefinitionBundle:{0}"),
)
SOURCE_DIR_FLAGS = ("-d", "--source-dir", "--sourcepath", "-p")
DEPLOY_WALK_CAP = 20000
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


def _store(workspace, client, change_id, source_dir: Path, job: str | None, how: str,
           org_alias: str | None = None, org_id_18: str | None = None) -> dict:
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
                                           "files": files, "sha256": _digest(files), "job": job,
                                           "org_alias": org_alias, "org_id_18": org_id_18})
    return _summary(event)


def _summary(event: dict) -> dict:
    return {"event_id": event["id"], "path": event["path"], "sha256": event["sha256"], "files": event["files"],
            "captured_at": event["created_at"], "job": event.get("job"), "how": event.get("summary"),
            "org_alias": event.get("org_alias"), "org_id_18": event.get("org_id_18")}


def import_before_state(workspace, client, change_id, source) -> dict:
    """Copy an earlier retrieve (a folder) or a record export (a JSON or CSV file, or a
    folder of them) into the change's evidence. The org it came from is not verified."""
    source = Path(source).expanduser().absolute()
    if source.is_symlink() or not (source.is_dir() or source.is_file()):
        raise ws.WorkspaceError(f"before-state must be a folder or a file (not a link): {source}")
    folder, _, _ = ws.load_client(workspace, client)
    clients = folder.parent
    resolved = source.resolve()
    if (clients == resolved or clients in resolved.parents) and not (folder == resolved or folder in resolved.parents):
        raise ws.WorkspaceError("the before-state belongs to a different client")
    if resolved.is_file():
        with tempfile.TemporaryDirectory(prefix="torque-before-") as tmp:
            shutil.copyfile(resolved, Path(tmp) / resolved.name)
            return _store(workspace, client, change_id, Path(tmp), None, "import")
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


def capture_metadata(workspace, client, change_id, org, components: list[str], run=subprocess.run,
                     org_id_18: str | None = None) -> dict:
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
        return _store(workspace, client, change_id, Path(tmp), result.get("id"), "retrieve", org, org_id_18)


def _record_file(sobject: str, record_id: str) -> str:
    return f"{sobject}__{record_id}.json"


def capture_records(workspace, client, change_id, org, records: list[str], run=subprocess.run,
                    org_id_18: str | None = None) -> dict:
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
        return _store(workspace, client, change_id, Path(tmp), None, "record read", org, org_id_18)


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


def component_for_path(relative: str) -> str:
    """The component a source-format file belongs to; File:<path> when unknown."""
    for pattern, template in SOURCE_PATTERNS:
        match = re.search(pattern, relative)
        if match:
            return template.format(*match.groups())
    return "File:" + relative


def _source_components(root: Path) -> list[str]:
    if root.is_file():
        return [component_for_path(root.as_posix())]
    out, seen = [], 0
    for folder, _dirs, files in os.walk(root):
        for name in files:
            seen += 1
            if seen > DEPLOY_WALK_CAP:
                raise ws.WorkspaceError(f"{root} holds more than {DEPLOY_WALK_CAP} files; deploy a narrower folder")
            path = Path(folder) / name
            out.append(component_for_path(path.relative_to(root.parent).as_posix()))
    return out


def deploy_components(argv: list[str], cwd: Path) -> list[str]:
    """Components a deploy names with --metadata, a manifest or a source folder."""
    legacy = argv_flags.is_legacy(argv)
    out: list[str] = list(argv_flags.values(argv, METADATA_FLAGS, legacy=legacy))
    for value in argv_flags.values(argv, MANIFEST_FLAGS, legacy=legacy):
        out += _manifest_components(Path(cwd) / value)
    source_flags = SOURCE_DIR_FLAGS if legacy or "deploy" in argv[:4] else ("-d", "--source-dir")
    for value in argv_flags.values(argv, source_flags, legacy=legacy):
        out += _source_components(Path(cwd) / value)
    return list(dict.fromkeys(out))


RECORD_WRITES = (("data", "update", "record"), ("data", "delete", "record"), ("data", "upsert", "record"))


def write_components(argv: list[str], cwd: Path) -> list[str]:
    """What a write changes, when Torque can list it: deploy components, or one
    record (Record:Object:Id). Empty means it cannot be listed (anonymous Apex, bulk
    loads, a deploy with no selector), so only a written recovery path covers it."""
    words = [w for w in argv[1:] if not w.startswith("-")][:3]
    if tuple(words) in RECORD_WRITES:
        sobject = argv_flags.values(argv, ("-s", "--sobject"))
        record_id = argv_flags.values(argv, ("-i", "--record-id"))
        return [f"Record:{sobject[0]}:{record_id[0]}"] if sobject and record_id else []
    if any(tok in ("deploy", "force:source:deploy", "force:mdapi:deploy") for tok in argv[1:4]):
        return deploy_components(argv, cwd)
    return []


def _needles(component: str) -> list[str]:
    kind, _, name = component.partition(":")
    patterns = TYPE_NEEDLES.get(kind)
    if not name or "*" in name:
        return []
    if kind == "File":
        return [name.rsplit("/", 1)[-1]]
    if patterns is None:
        return [name + ".", name + "/"]
    parent, _, child = name.partition(".")
    sobject, _, record_id = name.partition(":")
    return [p.format(name=name, parent=parent, child=child, record=_record_file(sobject, record_id))
            for p in patterns]


def _holds(path: str, needle: str) -> bool:
    path = "/" + path
    if needle.endswith("/"):
        return "/" + needle in path
    if needle.endswith("."):
        return ("/" + needle) in path
    return path.endswith("/" + needle)


def coverage(components: list[str], before: dict) -> list[str]:
    """Components the before-state does not contain (a wildcard is never covered)."""
    paths = [f["path"] for f in before.get("files", [])]
    return [c for c in components if not any(_holds(p, n) for n in _needles(c) for p in paths)]


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
