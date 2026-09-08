"""object_registry.py — the authoritative per-object metadata for the suite.

The committed config/object-registry.yaml is a baseline snapshot; generate_registry()
regenerates it from a live describe (Phase-0/refresh). load_registry() reads the YAML.
"""
from __future__ import annotations
import os
import re

from dataclasses import dataclass
from pathlib import Path
import yaml

_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "config" / "object-registry.yaml"


@dataclass
class ObjectEntry:
    api_name: str
    label: str
    crud_applicable: bool
    test_record_carrier: bool       # has Test_Record__c field
    parent_join_field: str | None   # lookup used by teardown for run-scoping
    seed_dependencies: list
    exclusion_reason: str | None


def load_registry(path: Path | None = None) -> dict[str, ObjectEntry]:
    selected = path or (Path(os.environ["TORQUE_BROWSER_REGISTRY"]) if os.environ.get("TORQUE_BROWSER_REGISTRY") else _REGISTRY_PATH)
    data = yaml.safe_load(selected.read_text()) or {}
    out = {}
    for api, row in (data.get("objects") or {}).items():
        out[api] = ObjectEntry(
            api_name=api,
            label=row.get("label", api),
            crud_applicable=bool(row.get("crud_applicable", True)),
            test_record_carrier=bool(row.get("test_record_carrier", False)),
            parent_join_field=row.get("parent_join_field"),
            seed_dependencies=row.get("seed_dependencies", []),
            exclusion_reason=row.get("exclusion_reason"),
        )
    return out


def generate_registry(sf_client, object_names: list[str], *, test_record_field: str = "Test_Record__c") -> dict:
    """Describe explicitly selected objects; caller supplies domain relationships.

    No JusticeServer carrier or parent assumptions are inferred. Registry files
    may provide parent_join_field/seed_dependencies for client-specific teardown.
    """
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", test_record_field):
        raise ValueError("Invalid test-record field")
    objects = {}
    for api in object_names:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", api):
            raise ValueError("Invalid object API name")
        rows = sf_client.query(f"SELECT QualifiedApiName FROM FieldDefinition WHERE EntityDefinition.QualifiedApiName='{api}'")
        fields = {r.get("QualifiedApiName") for r in rows}
        objects[api] = dict(label=api, crud_applicable=True, test_record_carrier=test_record_field in fields,
                            parent_join_field=None, seed_dependencies=[], exclusion_reason=None)
    return {"objects": objects}


