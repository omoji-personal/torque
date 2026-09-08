"""Resolve original metadata selectors to captured source, without broadening scope.

A retrieve can return parent containers as context. Their presence is not an
instruction to restore them. This module performs only local, read-only work.
"""
from __future__ import annotations

from fnmatch import fnmatchcase
import hashlib
import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET


class MetadataScopeError(ValueError):
    """The captured source cannot express the original recovery scope safely."""


def selected_records(payload: dict) -> list[dict]:
    selectors = payload.get("selectors") or {}
    if not isinstance(selectors, dict):
        raise MetadataScopeError("The original selector record is invalid.")
    requested = selectors.get("metadata_args") or []
    if not isinstance(requested, list) or not requested:
        raise MetadataScopeError("No original --metadata selectors are recorded; review the capture manually.")
    if any(selectors.get(key) for key in ("source_dirs", "manifest_paths", "destructive_manifest_paths")):
        raise MetadataScopeError("Mixed deploy selectors have no complete captured recovery scope; review them manually.")
    raw_records = payload.get("files")
    records = [entry for entry in raw_records if isinstance(entry, dict)] if isinstance(raw_records, list) else []
    selected = []
    for selector in requested:
        if not isinstance(selector, str) or not selector.strip():
            raise MetadataScopeError("The original metadata selector is invalid.")
        kind, colon, member = selector.partition(":")
        member = member if colon else "*"
        matches = [entry for entry in records if entry.get("type") == kind
                   and fnmatchcase(str(entry.get("fullName", "")), member)]
        if not matches or any(str(entry.get("before_state", "")).lower() != "present" for entry in matches):
            raise MetadataScopeError(f"No complete before-state for requested selector {selector!r}.")
        selected.extend(matches)
        # Selecting an entire object intentionally includes its captured children.
        # A CustomField selector never implies this parent selection.
        if kind == "CustomObject":
            names = {str(entry["fullName"]) for entry in matches}
            children = [entry for entry in records
                        if entry.get("type") in {"BusinessProcess", "CompactLayout", "CustomField", "FieldSet",
                                                 "Index", "ListView", "RecordType", "SharingReason", "ValidationRule", "WebLink"}
                        and any(str(entry.get("fullName", "")).startswith(name + ".") for name in names)]
            if any(str(entry.get("before_state", "")).lower() != "present" for entry in children):
                raise MetadataScopeError("An explicitly selected object has an incompletely captured child.")
            selected.extend(children)
    return list({(entry.get("type"), entry.get("fullName"), entry.get("filePath")): entry
                 for entry in selected}.values())



INVENTORY_FILE = ".capture-inventory.json"


def _no_symlink_path(path: Path) -> None:
    if ".." in path.parts or any(parent.is_symlink() for parent in (path, *path.parents)):
        raise MetadataScopeError("Captured source path contains traversal or a symlink.")


def _contained_file(path: Path, root: Path) -> Path:
    _no_symlink_path(path)
    _no_symlink_path(root)
    if not path.is_file():
        raise MetadataScopeError("Captured source file is missing.")
    canonical = path.resolve()
    if not canonical.is_relative_to(root.resolve()):
        raise MetadataScopeError("Captured source path is outside metadata-before.")
    return canonical


def write_capture_inventory(root: Path) -> None:
    """Index every source file at capture time, including unlisted companions."""
    root = root.absolute()
    _no_symlink_path(root)
    files = {}
    for path in sorted(root.rglob("*")):
        _no_symlink_path(path)
        if path.is_file() and path.name not in {INVENTORY_FILE, ".retrieve-result.json"}:
            files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    target = root / INVENTORY_FILE
    _no_symlink_path(target)
    target.write_text(json.dumps({"schema": 1, "algorithm": "sha256", "files": files}, indent=2) + "\n")
    target.chmod(0o600)


def _file_index(payload: dict, root: Path) -> dict[Path, str]:
    index = {}
    inventory = root / INVENTORY_FILE
    if inventory.exists():
        _contained_file(inventory, root)
        try:
            record = json.loads(inventory.read_text())
            assert record["schema"] == 1 and record["algorithm"] == "sha256" and isinstance(record["files"], dict)
            for name, checksum in record["files"].items():
                relative = Path(name)
                assert not relative.is_absolute() and ".." not in relative.parts
                assert isinstance(checksum, str) and re.fullmatch(r"[0-9a-f]{64}", checksum)
                index[root / relative] = checksum
        except (ValueError, TypeError, KeyError, AssertionError) as exc:
            raise MetadataScopeError("Capture-time source inventory is invalid.") from exc
    for record in payload.get("files", []):
        if not isinstance(record, dict) or not isinstance(record.get("filePath"), str):
            continue
        path = Path(record["filePath"])
        path = path if path.is_absolute() else root / path
        if ".." in path.parts or not path.is_relative_to(root):
            continue
        checksum = record.get("before_checksum")
        if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            continue
        if path in index and index[path] != checksum:
            raise MetadataScopeError("Captured manifest and source inventory disagree.")
        index[path] = checksum
    return index


def _verified(path: Path, root: Path, index: dict[Path, str]) -> Path:
    path = _contained_file(path, root)
    expected = index.get(path)
    if not expected:
        raise MetadataScopeError("Required source/companion has no capture-time checksum inventory; review legacy recovery manually.")
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise MetadataScopeError("Captured source no longer matches its recorded checksum.")
    return path


def _logical_relative(path: Path, root: Path) -> Path:
    relative = path.relative_to(root)
    if len(relative.parts) > 3 and relative.parts[1:3] == ("main", "default"):
        relative = Path(*relative.parts[3:])
    return relative


def _component_paths(entry: dict, path: Path, root: Path, index: dict[Path, str]) -> set[Path]:
    from .snapshot_pre import _PATH_CONVENTIONS
    kind, name = entry["type"], entry["fullName"]
    logical = _logical_relative(path, root)
    paired = {"ApexClass": ("classes", ".cls"), "ApexTrigger": ("triggers", ".trigger"),
              "ApexPage": ("pages", ".page"), "ApexComponent": ("components", ".component")}
    if kind in paired:
        folder, suffix = paired[kind]
        expected = Path(folder) / (name + suffix)
        if logical not in {expected, Path(str(expected) + "-meta.xml")}:
            raise MetadataScopeError("Captured source path does not match the selected component name/type.")
        body = path.with_name(name + suffix)
        return {_verified(body, root, index), _verified(Path(str(body) + "-meta.xml"), root, index)}
    if kind in {"AuraDefinitionBundle", "LightningComponentBundle"}:
        folder = "aura" if kind == "AuraDefinitionBundle" else "lwc"
        if len(logical.parts) < 3 or logical.parts[:2] != (folder, name):
            raise MetadataScopeError("Captured bundle path does not match its selected component.")
        bundle = path
        for _ in logical.parts[2:]: bundle = bundle.parent
        current = {child for child in bundle.rglob("*") if child.is_file() or child.is_symlink()}
        captured = {child for child in index if child.is_relative_to(bundle)}
        if current != captured:
            raise MetadataScopeError("Bundle files differ from the capture-time inventory.")
        if kind == "LightningComponentBundle":
            required = {bundle / (name + ".js"), bundle / (name + ".js-meta.xml")}
            if not required <= captured:
                raise MetadataScopeError("Required Lightning component source/metadata is missing.")
        elif not any((bundle / (name + suffix)) in captured for suffix in (".cmp", ".app", ".evt", ".intf", ".tokens")):
            raise MetadataScopeError("Required Aura bundle definition is missing.")
        return {_verified(child, root, index) for child in captured}
    if kind == "StaticResource":
        prefix = Path("staticresources")
        if not (logical in {prefix / (name + ".resource"), prefix / (name + ".resource-meta.xml")}
                or (len(logical.parts) >= 3 and logical.parts[:2] == ("staticresources", name))):
            raise MetadataScopeError("Captured resource path does not match its selected component.")
        base = path
        for _ in logical.parts[1:]: base = base.parent
        meta, body, folder = base / (name + ".resource-meta.xml"), base / (name + ".resource"), base / name
        files = {_verified(meta, root, index)}
        if body.is_file():
            files.add(_verified(body, root, index))
        elif folder.is_dir():
            _no_symlink_path(folder)
            current = {child for child in folder.rglob("*") if child.is_file() or child.is_symlink()}
            captured = {child for child in index if child.is_relative_to(folder)}
            if not captured or current != captured:
                raise MetadataScopeError("Resource files differ from capture-time inventory.")
            files.update(_verified(child, root, index) for child in captured)
        else:
            raise MetadataScopeError("Required static resource body is missing.")
        return files
    conventions = {**_PATH_CONVENTIONS, "CustomLabels": ("labels", "CustomLabels.labels-meta.xml")}
    convention = conventions.get(kind)
    if convention is None:
        raise MetadataScopeError(f"No verified source-path convention for {kind}; review recovery manually.")
    folder, filename = convention
    values = {"name": name}
    if "{parent}" in folder:
        if "." not in name:
            raise MetadataScopeError("Selected child component has no parent-qualified name.")
        values["parent"], values["name"] = name.split(".", 1)
    if logical != Path(folder.format(**values)) / filename.format(**values):
        raise MetadataScopeError("Captured source path does not match the selected component name/type.")
    return {_verified(path, root, index)}


def recovery_files(payload: dict, snapshot_dir: Path) -> list[Path]:
    """Return exact, capture-indexed files with complete required companions."""
    snapshot_dir = snapshot_dir.absolute()
    _no_symlink_path(snapshot_dir)
    root = (snapshot_dir / "metadata-before").resolve()
    _no_symlink_path(snapshot_dir / "metadata-before")
    records = selected_records(payload)
    index = _file_index(payload, root)
    result = set()
    for entry in records:
        raw = entry.get("filePath")
        if not isinstance(raw, str) or not raw:
            raise MetadataScopeError("Selected component has no captured source path.")
        path = Path(raw)
        path = _contained_file(path if path.is_absolute() else root / path, root)
        files = _component_paths(entry, path, root, index)
        for candidate in files:
            if candidate.name.endswith("-meta.xml"):
                try:
                    actual = ET.parse(candidate).getroot().tag.rsplit("}", 1)[-1]
                except ET.ParseError as exc:
                    raise MetadataScopeError("Selected source metadata XML is invalid.") from exc
                if actual != entry["type"]:
                    raise MetadataScopeError(f"Captured {actual} container cannot restore just {entry['type']}.")
        result.update(files)
    return sorted(result)


def captured_source_relative(path: Path) -> Path:
    """Retain source structure without returning traversal or symlink paths."""
    _no_symlink_path(path)
    root = next((parent for parent in path.parents if parent.name == "metadata-before"), None)
    if root is None:
        raise MetadataScopeError("File staging requires a captured metadata-before source path.")
    relative = _contained_file(path, root).relative_to(root.resolve())
    if relative.is_absolute() or ".." in relative.parts:
        raise MetadataScopeError("Invalid captured source relative path.")
    return relative
