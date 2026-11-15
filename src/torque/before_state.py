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
# Each entry owns its recovery alternatives, source mapping and payload binding.
# Completeness requirements (for example Apex companions) remain explicit.
METADATA_LAYOUTS = {}


def _layout(kind, alternatives, source=(), binding=()):
    METADATA_LAYOUTS[kind] = (alternatives, source, binding)


for _kind, _folder, _suffix in (
    ("Flow", "flows", "flow"), ("Layout", "layouts", "layout"),
    ("FlexiPage", "flexipages", "flexipage"), ("PermissionSet", "permissionsets", "permissionset"),
    ("CustomMetadata", "customMetadata", "md"), ("PermissionSetGroup", "permissionsetgroups", "permissionsetgroup"),
    ("Profile", "profiles", "profile"), ("QuickAction", "quickActions", "quickAction"),
    ("GlobalValueSet", "globalValueSets", "globalValueSet"), ("StandardValueSet", "standardValueSets", "standardValueSet"),
    ("CustomTab", "tabs", "tab"), ("CustomApplication", "applications", "app"),
    ("CustomPermission", "customPermissions", "customPermission"),
):
    _layout(_kind, (f"{_folder}/{{name}}.{_suffix}-meta.xml", f"{_folder}/{{name}}.{_suffix}"),
            ((rf"(?:^|/){_folder}/([^/]+)\.{_suffix}(?:-meta\.xml)?$", _kind + ":{0}"),))

for _kind, _folder, _suffix in (
    ("ApexClass", "classes", "cls"), ("ApexTrigger", "triggers", "trigger"),
    ("ApexPage", "pages", "page"), ("ApexComponent", "components", "component"),
):
    _layout(_kind, ((f"{_folder}/{{name}}.{_suffix}", f"{_folder}/{{name}}.{_suffix}-meta.xml"),),
            ((rf"(?:^|/){_folder}/([^/]+)\.{_suffix}(?:-meta\.xml)?$", _kind + ":{0}"),))

for _kind, _folder, _suffix in (
    ("CustomField", "fields", "field"), ("ValidationRule", "validationRules", "validationRule"),
    ("RecordType", "recordTypes", "recordType"), ("ListView", "listViews", "listView"),
    ("CompactLayout", "compactLayouts", "compactLayout"), ("FieldSet", "fieldSets", "fieldSet"),
    ("WebLink", "webLinks", "webLink"), ("BusinessProcess", "businessProcesses", "businessProcess"),
    ("Index", "indexes", "index"),
):
    _layout(_kind, (f"objects/{{parent}}/{_folder}/{{child}}.{_suffix}-meta.xml", "objects/{parent}.object"),
            ((rf"(?:^|/)objects/([^/]+)/{_folder}/([^/]+)\.{_suffix}-meta\.xml$", _kind + ":{0}.{1}"),),
            (("/objects/{parent}/", "/objects/{parent}.") if _kind in
             ("CompactLayout", "FieldSet", "WebLink", "BusinessProcess", "Index") else
             (f"/objects/{{parent}}/{_folder}/{{child}}.", "/objects/{parent}.")))

for _parent_kind, _children, _folder, _suffix in (
    ("Workflow", ("WorkflowRule", "WorkflowFieldUpdate", "WorkflowAlert", "WorkflowOutboundMessage", "WorkflowTask"), "workflows", "workflow"),
    ("SharingRules", ("SharingCriteriaRule", "SharingOwnerRule"), "sharingRules", "sharingRules"),
    ("AssignmentRules", ("AssignmentRule",), "assignmentRules", "assignmentRules"),
    ("AutoResponseRules", ("AutoResponseRule",), "autoResponseRules", "autoResponseRules"),
    ("EscalationRules", ("EscalationRule",), "escalationRules", "escalationRules"),
    ("MatchingRules", ("MatchingRule",), "matchingRules", "matchingRule"),
):
    _layout(_parent_kind, (f"{_folder}/{{name}}.{_suffix}-meta.xml", f"{_folder}/{{name}}.{_suffix}"),
            ((rf"(?:^|/){_folder}/([^/]+)\.{_suffix}(?:-meta\.xml)?$", _parent_kind + ":{0}"),),
            (f"/{_folder}/{{name}}.{_suffix}",))
    for _child_kind in _children:
        _layout(_child_kind, (f"{_folder}/{{parent}}.{_suffix}-meta.xml", f"{_folder}/{{parent}}.{_suffix}"),
                binding=(f"/{_folder}/{{parent}}.{_suffix}",))

_layout("CustomObject", ("objects/{name}/{name}.object-meta.xml", "objects/{name}.object"),
        ((r"(?:^|/)objects/([^/]+)/[^/]+\.object-meta\.xml$", "CustomObject:{0}"),),
        ("/objects/{name}/", "/objects/{name}."))
_layout("LightningComponentBundle", (("lwc/{name}/{name}.js", "lwc/{name}/{name}.js-meta.xml"),),
        ((r"(?:^|/)lwc/([^/]+)/", "LightningComponentBundle:{0}"),), ("/lwc/{name}/",))
_layout("AuraDefinitionBundle", tuple("aura/{name}/{name}." + suffix for suffix in ("cmp", "app", "evt", "intf", "tokens")),
        ((r"(?:^|/)aura/([^/]+)/", "AuraDefinitionBundle:{0}"),), ("/aura/{name}/",))
_layout("StaticResource", (("staticresources/{name}.resource-meta.xml", "staticresources/{name}.resource"),
                           ("staticresources/{name}.resource-meta.xml", "staticresources/{name}/")),
        binding=("/staticresources/{name}.", "/staticresources/{name}/"))
for _kind in ("CustomLabel", "CustomLabels"):
    _layout(_kind, ("labels/CustomLabels.labels-meta.xml", "labels/CustomLabels.labels"),
            binding=("/labels/",))
_layout("Record", ("records/{record}",))
# These keep their existing generic recovery matching and broader file bindings.
for _kind, _binding in (
    ("ExperienceBundle", ("/experiences/{name}/", "/experiences/{name}.")),
    ("Document", ("/documents/{name}",)), ("EmailTemplate", ("/email/{name}.", "/email/{name}/")),
):
    _layout(_kind, None, binding=_binding)

TYPE_NEEDLES = {kind: layout[0] for kind, layout in METADATA_LAYOUTS.items() if layout[0] is not None}
SOURCE_PATTERNS = tuple(pattern for layout in METADATA_LAYOUTS.values() for pattern in layout[1])
CONTAINER_NEEDLES = {kind: layout[2] for kind, layout in METADATA_LAYOUTS.items() if layout[2]}
SOURCE_DIR_FLAGS = ("-d", "--source-dir", "--sourcepath", "-p")
DESTRUCTIVE_FLAGS = ("--pre-destructive-changes", "--post-destructive-changes", "--predestructivechanges",
                     "--postdestructivechanges")


def payload_needles(component: str) -> list[str]:
    """Path fragments of the local files a component is deployed from."""
    kind, _, name = component.partition(":")
    parent, _, child = name.partition(".")
    if kind in CONTAINER_NEEDLES:
        return [n.format(name=name, parent=parent, child=child) for n in CONTAINER_NEEDLES[kind]]
    return ["/" + n for n in _needles(component)] + ["/" + name.split(".")[-1] + "."]
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


# Windows refuses a file path of 260 characters or more, and a folder path of 248
# or more, unless long paths are enabled on the PC.
_WINDOWS_FILE_LIMIT, _WINDOWS_FOLDER_LIMIT = 259, 247


def _long_paths_enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            return winreg.QueryValueEx(key, "LongPathsEnabled")[0] == 1
    except (ImportError, OSError):
        return False


def path_limit_problem(source_dir: Path, target: Path, limited: bool | None = None) -> str:
    """Why the tree under source_dir cannot be copied to target on this PC, or "".
    Retrieved metadata is deep (objects/NAME/fields/NAME.field-meta.xml) and the
    evidence folder adds to it, so a copy can pass Windows' path limit half way."""
    if limited is None:
        limited = os.name == "nt" and not _long_paths_enabled()
    if not limited:
        return ""
    base = len(str(target)) + 1
    worst = max((base + len(str(path.relative_to(source_dir)))
                 - (_WINDOWS_FOLDER_LIMIT if path.is_dir() else _WINDOWS_FILE_LIMIT)
                 for path in source_dir.rglob("*")), default=0)
    if worst <= 0:
        return ""
    return (f"the before-state would pass Windows' path limit by {worst} character(s): its files go under "
            f"{target}, and long paths are not enabled on this PC. Nothing was stored. Use a shorter workspace "
            "path, or have long paths enabled in Windows (LongPathsEnabled)")


def _store(workspace, client, change_id, source_dir: Path, job: str | None, how: str,
           org_alias: str | None = None, org_id_18: str | None = None) -> dict:
    root, record = changes.load_change(workspace, client, change_id)
    evidence = ws._inside(root, root / "evidence")
    target = ws._inside(root, evidence / ("before-" + uuid4().hex))
    problem = path_limit_problem(source_dir, target)
    if problem:
        raise ws.WorkspaceError(problem)
    evidence.mkdir(mode=0o700, exist_ok=True)
    shutil.copytree(source_dir, target, symlinks=True, copy_function=ws.copy_private)
    files = []
    for path in sorted(target.rglob("*")):
        if path.is_symlink():
            shutil.rmtree(target)
            raise ws.WorkspaceError(f"a before-state must not contain links: {path.relative_to(target)}")
        if path.is_file():
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


def _export_records(path: Path, sobject: str | None) -> list[dict]:
    """Records in a JSON export (sf data query --json, sf data export tree, or a list of
    records with attributes.type) or a CSV export (with `sobject` naming the object)."""
    records: list[dict] = []
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if isinstance(data, dict):
            result = data.get("result")
            data = (result.get("records") if isinstance(result, dict) else None) or data.get("records") or []
        for row in data if isinstance(data, list) else []:
            if not isinstance(row, dict):
                continue
            kind = (row.get("attributes") or {}).get("type") if isinstance(row.get("attributes"), dict) else None
            kind = kind or sobject
            record_id = row.get("Id") or row.get("id")
            if isinstance(kind, str) and isinstance(record_id, str) and kind and record_id:
                records.append({"type": kind, "id": record_id, "fields": row})
    elif path.suffix.lower() == ".csv" and sobject:
        import csv
        try:
            with path.open(newline="", encoding="utf-8-sig") as handle:
                for row in csv.DictReader(handle):
                    record_id = row.get("Id") or row.get("ID") or row.get("id")
                    if record_id:
                        records.append({"type": sobject, "id": record_id, "fields": dict(row)})
        except (OSError, UnicodeError, csv.Error):
            return []
    return records


def _normalize_exports(folder: Path, sobject: str | None) -> None:
    """Write each exported record as records/Object__Id.json, the form coverage reads,
    next to the original export files."""
    exports = [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in (".json", ".csv")
               and "records" not in p.relative_to(folder).parts[:1]]
    for export in exports:
        for record in _export_records(export, sobject):
            if not str(record["type"]).replace("_", "").isalnum() or not str(record["id"]).isalnum():
                continue
            target = folder / "records" / _record_file(record["type"], record["id"])
            target.parent.mkdir(exist_ok=True)
            target.write_text(json.dumps(record["fields"], indent=2), encoding="utf-8")


def import_before_state(workspace, client, change_id, source, sobject: str | None = None) -> dict:
    """Copy an earlier retrieve (a folder) or a record export (a JSON or CSV file, or a
    folder of them) into the change's evidence. Exported records are also stored as
    records/Object__Id.json, so the grant can check them against a record write (a CSV
    export needs `sobject`). The org it came from is not verified."""
    ws.require_writable(workspace)
    source = Path(source).expanduser().absolute()
    if source.is_symlink() or not (source.is_dir() or source.is_file()):
        raise ws.WorkspaceError(f"before-state must be a folder or a file (not a link): {source}")
    folder, _, _ = ws.load_client(workspace, client)
    clients = folder.parent
    resolved = source.resolve()
    if (clients == resolved or clients in resolved.parents) and not (folder == resolved or folder in resolved.parents):
        raise ws.WorkspaceError("the before-state belongs to a different client")
    if sobject is not None and not sobject.replace("_", "").isalnum():
        raise ws.WorkspaceError(f"not an object name: {sobject!r}")
    with tempfile.TemporaryDirectory(prefix="torque-before-") as tmp:
        staging = Path(tmp) / "import"
        if resolved.is_file():
            staging.mkdir()
            shutil.copyfile(resolved, staging / resolved.name)
        else:
            shutil.copytree(resolved, staging, symlinks=True)
        _normalize_exports(staging, sobject)
        return _store(workspace, client, change_id, staging, None, "import")


def _sf_json(run, cmd: list[str], timeout: int) -> dict:
    if run is None:
        # The shared launcher finds sf where Windows installs it as a batch file.
        from jsc_common.tools import run
    try:
        done = run(cmd, capture_output=True, text=True, timeout=timeout)
        data = json.loads(done.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ws.WorkspaceError(f"{' '.join(cmd[:4])} failed; no before-state recorded ({exc})") from exc
    if done.returncode != 0 or not isinstance(data, dict) or data.get("status") != 0:
        message = str(data.get("message") if isinstance(data, dict) else "") or (done.stderr or "")
        raise ws.WorkspaceError(f"{' '.join(cmd[:4])} failed; no before-state recorded: {message[:300]}")
    return data


def capture_metadata(workspace, client, change_id, org, components: list[str], run=None,
                     org_id_18: str | None = None) -> dict:
    """Retrieve the named components now, as their own recorded step."""
    ws.require_writable(workspace)
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


def capture_records(workspace, client, change_id, org, records: list[str], run=None,
                    org_id_18: str | None = None) -> dict:
    """Read each record now (Object:Id), as its own recorded step."""
    ws.require_writable(workspace)
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


def _source_components(root: Path, cwd: Path | None = None) -> list[str]:
    def component(path):
        try:
            relative = Path(os.path.relpath(path, cwd)) if cwd is not None else path
        except ValueError:  # Windows paths on different drives have no relative form.
            relative = path
        return component_for_path(relative.as_posix())

    if root.is_file():
        return [component(root)]
    out, seen = [], 0
    for folder, _dirs, files in os.walk(root):
        for name in files:
            seen += 1
            if seen > DEPLOY_WALK_CAP:
                raise ws.WorkspaceError(f"{root} holds more than {DEPLOY_WALK_CAP} files; deploy a narrower folder")
            path = Path(folder) / name
            out.append(component(path))
    return out


def destructive_components(argv: list[str], cwd: Path) -> list[str]:
    """Components a deploy deletes, from its pre- and post-destructive manifests."""
    out: list[str] = []
    for value in argv_flags.values(argv, DESTRUCTIVE_FLAGS, legacy=argv_flags.is_legacy(argv)):
        out += _manifest_components(Path(cwd) / value)
    return list(dict.fromkeys(out))


def deploy_components(argv: list[str], cwd: Path) -> list[str]:
    """Components a deploy sends, named with --metadata, a manifest or a source folder
    (not the ones it deletes: see destructive_components)."""
    legacy = argv_flags.is_legacy(argv)
    out: list[str] = list(argv_flags.values(argv, METADATA_FLAGS, legacy=legacy))
    for value in argv_flags.values(argv, MANIFEST_FLAGS, legacy=legacy):
        out += _manifest_components(Path(cwd) / value)
    source_flags = SOURCE_DIR_FLAGS if legacy or "deploy" in argv[:4] else ("-d", "--source-dir")
    for value in argv_flags.values(argv, source_flags, legacy=legacy):
        out += _source_components(Path(cwd) / value, cwd)
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
        return list(dict.fromkeys(deploy_components(argv, cwd) + destructive_components(argv, cwd)))
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
    flat = [n for p in patterns for n in (p if isinstance(p, tuple) else (p,))]
    return [p.format(name=name, parent=parent, child=child, record=_record_file(sobject, record_id))
            for p in flat]


def _holds(path: str, needle: str) -> bool:
    path = "/" + path
    if needle.endswith("/"):
        return "/" + needle in path
    if needle.endswith("."):
        return ("/" + needle) in path
    return path.endswith("/" + needle)


def _alternatives(component: str) -> list[tuple[str, ...]]:
    """Each way a before-state can hold a component: a set of files that must all be present."""
    kind, _, name = component.partition(":")
    patterns = TYPE_NEEDLES.get(kind)
    if patterns is None or not name or "*" in name:
        return [(n,) for n in _needles(component)]
    parent, _, child = name.partition(".")
    sobject, _, record_id = name.partition(":")
    values = dict(name=name, parent=parent, child=child, record=_record_file(sobject, record_id))
    return [tuple(p.format(**values) for p in (pattern if isinstance(pattern, tuple) else (pattern,)))
            for pattern in patterns]


def coverage(components: list[str], before: dict) -> list[str]:
    """Components the before-state cannot restore (a wildcard is never covered): each
    needs every file its recovery uses, such as Apex source with its -meta.xml, or a
    Lightning component's JavaScript with its -meta.xml."""
    paths = [f["path"] for f in before.get("files", [])]
    return [c for c in components
            if not any(all(any(_holds(p, n) for p in paths) for n in group) for group in _alternatives(c))]


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
