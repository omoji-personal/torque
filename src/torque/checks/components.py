"""Read Salesforce metadata files into components.

Source format (`*.field-meta.xml`, `*.flow-meta.xml`, ...) and, where cheap, Metadata API format
(`objects/X.object` with its fields, validation rules and record types inside, `flows/X.flow`, ...).
A file that cannot be parsed becomes one component with `error` set; it never stops the run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from urllib.parse import unquote

from .xmlread import Node, XmlError, parse

SOURCE_SUFFIXES = [
    (".field-meta.xml", "CustomField"), (".object-meta.xml", "CustomObject"), (".flow-meta.xml", "Flow"),
    (".validationrule-meta.xml", "ValidationRule"), (".permissionset-meta.xml", "PermissionSet"),
    (".permissionsetgroup-meta.xml", "PermissionSetGroup"), (".recordtype-meta.xml", "RecordType"),
    (".custompermission-meta.xml", "CustomPermission"), (".profile-meta.xml", "Profile"),
    (".flexipage-meta.xml", "FlexiPage"), (".layout-meta.xml", "Layout"), (".md-meta.xml", "CustomMetadata"),
    (".settings-meta.xml", "Settings"), (".namedcredential-meta.xml", "NamedCredential"),
    (".externalcredential-meta.xml", "ExternalCredential"),
]
MDAPI_SUFFIXES = [
    (".object", "CustomObject"), (".flow", "Flow"), (".permissionset", "PermissionSet"),
    (".permissionsetgroup", "PermissionSetGroup"), (".custompermission", "CustomPermission"),
    (".profile", "Profile"), (".flexipage", "FlexiPage"), (".layout", "Layout"), (".md", "CustomMetadata"),
    (".settings", "Settings"), (".namedcredential", "NamedCredential"),
    (".externalcredential", "ExternalCredential"),
]
CODE_SUFFIXES = [(".cls", "ApexClass"), (".trigger", "ApexTrigger")]
BUNDLE_TYPES = {"lwc": "LightningComponentBundle", "aura": "AuraDefinitionBundle"}
SKIP_DIRS = {".git", "node_modules", ".sf", ".sfdx", "__tests__", ".localdevserver", ".vscode", "__pycache__"}
# Fields inside a Metadata API object file, by tag.
MDAPI_CHILDREN = {"fields": "CustomField", "validationRules": "ValidationRule", "recordTypes": "RecordType"}
CUSTOM_SUFFIX = re.compile(r"__(c|mdt|e|b|x)$", re.I)


@dataclass(eq=False)
class Component:
    type: str
    name: str
    path: str                       # absolute file path, or the bundle folder
    node: Node | None = None        # parsed XML (None for Apex, bundles and unreadable files)
    source: str | None = None       # Apex source text
    fmt: str = "source"             # "source" or "mdapi"
    error: str | None = None        # why the file could not be read
    error_line: int | None = None
    status: str = "unknown"         # new, changed, unchanged, failed, unknown
    state: str = ""                 # the state a deploy result gave, as written there
    baseline: "Component | None" = None
    extra: dict = field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"{self.type}:{self.name}"

    @property
    def key(self) -> tuple[str, str]:
        return (self.type, self.name.lower())

    @property
    def line(self) -> int | None:
        return self.node.line if self.node is not None else None

    def fingerprint(self):
        if self.node is not None:
            return ("xml", self.node.fingerprint())
        if self.source is not None:
            return ("text", re.sub(r"\s+", "", self.source))
        return ("none", self.error or "")


@dataclass
class Project:
    root: str | None = None             # folder holding sfdx-project.json
    source_api_version: str | None = None
    namespace: str = ""


def is_custom(api_name: str) -> bool:
    return bool(CUSTOM_SUFFIX.search(api_name))


def classify(path: str) -> tuple[str, str, str] | None:
    """(type, suffix, format) for a metadata file, or None."""
    low = os.path.basename(path).lower()
    if low.endswith("-meta.xml"):
        for suffix, typ in SOURCE_SUFFIXES:
            if low.endswith(suffix):
                return typ, suffix, "source"
        return None   # companions (.cls-meta.xml) and types without rules
    for suffix, typ in CODE_SUFFIXES:
        if low.endswith(suffix):
            return typ, suffix, "source"
    for suffix, typ in MDAPI_SUFFIXES:
        if low.endswith(suffix):
            if typ == "CustomMetadata" and Path(path).parent.name.lower() != "custommetadata":
                return None   # a Markdown file, not a custom metadata record
            return typ, suffix, "mdapi"
    return None


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def read_file(path: str) -> list[Component]:
    """The components one file holds. Unreadable files give one component with `error`."""
    hit = classify(path)
    if not hit:
        return []
    typ, suffix, fmt = hit
    base = os.path.basename(path)
    stem = unquote(base[: len(base) - len(suffix)])
    parts = Path(path).parts
    if typ in ("ApexClass", "ApexTrigger"):
        try:
            text = _read_bytes(path).decode("utf-8", errors="replace")
        except OSError as exc:
            return [Component(typ, stem, path, error=f"could not read the file ({exc.strerror or exc})")]
        return [Component(typ, stem, path, source=text)]
    if typ in ("CustomField", "ValidationRule", "RecordType") and fmt == "source":
        name = f"{parts[-3] if len(parts) >= 3 else '?'}.{stem}"
    else:
        name = stem
    try:
        node = parse(_read_bytes(path))
    except XmlError as exc:
        return [Component(typ, name, path, fmt=fmt, error=str(exc), error_line=exc.line)]
    except OSError as exc:
        return [Component(typ, name, path, fmt=fmt, error=f"could not read the file ({exc.strerror or exc})")]
    if typ == "CustomObject" and fmt == "mdapi":
        return _mdapi_object(node, stem, path)
    return [Component(typ, name, path, node=node, fmt=fmt)]


def _mdapi_object(node: Node, obj: str, path: str) -> list[Component]:
    out = []
    defines = any(node.child(k) is not None for k in ("label", "nameField", "deploymentStatus", "customSettingsType"))
    if defines or is_custom(obj):
        out.append(Component("CustomObject", obj, path, node=node, fmt="mdapi"))
    for tag, typ in MDAPI_CHILDREN.items():
        for c in node.all(tag):
            out.append(Component(typ, f"{obj}.{c.value('fullName')}", path, node=c, fmt="mdapi"))
    return out


def walk(root: str):
    if os.path.isfile(root):
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for f in sorted(filenames):
            yield os.path.join(dirpath, f)


def bundles(root: str) -> list[Component]:
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, _files in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        kind = BUNDLE_TYPES.get(os.path.basename(os.path.dirname(dirpath)).lower())
        if kind:
            out.append(Component(kind, os.path.basename(dirpath), dirpath))
            dirnames[:] = []
    return out


def read_tree(paths: list[str]) -> list[Component]:
    """Every component under the given folders and files, in a stable order."""
    out: list[Component] = []
    seen: set[str] = set()
    for root in paths:
        root = os.path.abspath(root)
        for f in walk(root):
            key = os.path.normcase(f)
            if key in seen:
                continue
            seen.add(key)
            out.extend(read_file(f))
        for b in bundles(root):
            if os.path.normcase(b.path) not in seen:
                seen.add(os.path.normcase(b.path))
                out.append(b)
    return out


def find_project(paths: list[str]) -> Project:
    """The sfdx-project.json at or above the first path."""
    if not paths:
        return Project()
    here = Path(os.path.abspath(paths[0]))
    if here.is_file():
        here = here.parent
    for folder in (here, *here.parents):
        candidate = folder / "sfdx-project.json"
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                return Project(root=str(folder))
            if not isinstance(data, dict):
                return Project(root=str(folder))
            version = data.get("sourceApiVersion")
            return Project(root=str(folder), source_api_version=str(version) if version else None,
                           namespace=str(data.get("namespace") or ""))
    return Project()
