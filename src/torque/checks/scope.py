"""Which components are the change: a deploy result, a baseline folder, a list, files, or everything."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import os
from pathlib import PurePath

from .components import BUNDLE_TYPES, CODE_SUFFIXES, MDAPI_SUFFIXES, SOURCE_SUFFIXES, Component, read_tree
from . import deploy_result

READ_TYPES = ({t for _, t in SOURCE_SUFFIXES} | {t for _, t in MDAPI_SUFFIXES} | {t for _, t in CODE_SUFFIXES}
              | set(BUNDLE_TYPES.values()))
GRANT_TYPES = ("PermissionSet", "Profile")
STATE_STATUS = {"Created": "new", "Changed": "changed", "Unchanged": "unchanged", "Failed": "failed"}


class ScopeError(ValueError):
    """The scope input cannot be used; the message says what to do."""


@dataclass
class Scope:
    mode: str                                   # changed-from, baseline, only, files, all
    source: str | None
    everything: list[Component]
    in_scope: list[Component]
    grant_scope: list[Component]
    baseline: list[Component] | None = None
    unchanged: int = 0
    outside: int = 0                            # read under PATH but not part of the change
    not_found: list[dict] = field(default_factory=list)
    deleted: list[dict] = field(default_factory=list)
    unsupported: Counter = field(default_factory=Counter)   # in the change, of a type with no rules here
    result: deploy_result.DeployResult | None = None

    def status_counts(self) -> dict:
        return dict(Counter(c.status for c in self.in_scope))


def _tail(path: str, n: int = 4) -> tuple:
    parts = PurePath(path.replace("\\", "/")).parts
    return tuple(p.lower() for p in parts[-n:])


def changed_from(paths: list[str], result_path: str) -> Scope:
    result = deploy_result.read(result_path)
    everything = read_tree(paths)
    by_key: dict[tuple, list[Component]] = {}
    by_tail: dict[tuple, list[Component]] = {}
    for c in everything:
        by_key.setdefault(c.key, []).append(c)
        by_tail.setdefault(_tail(c.path), []).append(c)
    scope = Scope("changed-from", result_path, everything, [], [], result=result)
    chosen: set[int] = set()
    for entry in result.components:
        if entry.state == "Deleted":
            scope.deleted.append({"type": entry.type, "name": entry.name})
            continue
        matches = by_key.get((entry.type, entry.name.lower()))
        if not matches and entry.file_path:
            matches = [c for c in by_tail.get(_tail(entry.file_path), []) if c.type == entry.type]
        if not matches:
            if entry.type in READ_TYPES:
                scope.not_found.append({"type": entry.type, "name": entry.name, "state": entry.state})
            elif entry.state != "Unchanged":
                scope.unsupported[entry.type] += 1
            continue
        for c in matches:
            c.state = entry.state
            c.status = STATE_STATUS.get(entry.state, "unknown")
            if c.type in GRANT_TYPES:
                scope.grant_scope.append(c)
            if c.status == "unchanged":
                scope.unchanged += 1
            elif id(c) not in chosen:
                chosen.add(id(c))
                scope.in_scope.append(c)
    scope.outside = sum(1 for c in everything if not c.state)
    return scope


def with_baseline(paths: list[str], baseline_dir: str) -> Scope:
    if not os.path.isdir(baseline_dir):
        raise ScopeError(f"--baseline {baseline_dir} is not a folder; give the folder retrieved before the change")
    everything = read_tree(paths)
    base = read_tree([baseline_dir])
    index = {}
    for b in base:
        index.setdefault(b.key, b)
    scope = Scope("baseline", baseline_dir, everything, [], [c for c in everything if c.type in GRANT_TYPES],
                  baseline=base)
    for c in everything:
        b = index.get(c.key)
        if b is None:
            c.status = "new"
        elif b.fingerprint() == c.fingerprint():
            c.status = "unchanged"
            scope.unchanged += 1
            continue
        else:
            c.status, c.baseline = "changed", b
        scope.in_scope.append(c)
    return scope


def only(paths: list[str], items: list[str]) -> Scope:
    wanted = []
    for item in items:
        typ, sep, name = item.partition(":")
        if not sep or not typ or not name:
            raise ScopeError(f"--only {item!r}: write Type:Name, for example CustomField:Account.Region__c or "
                             "Flow:Donation_Thank_You")
        wanted.append((typ, name))
    everything = read_tree(paths)
    by_key: dict[tuple, list[Component]] = {}
    for c in everything:
        by_key.setdefault((c.type.lower(), c.name.lower()), []).append(c)
    scope = Scope("only", None, everything, [], [])
    for typ, name in wanted:
        hits = by_key.get((typ.lower(), name.lower()))
        if not hits:
            scope.not_found.append({"type": typ, "name": name, "state": ""})
            continue
        for c in hits:
            if c not in scope.in_scope:
                scope.in_scope.append(c)
                if c.type in GRANT_TYPES:
                    scope.grant_scope.append(c)
    scope.outside = len(everything) - len(scope.in_scope)
    return scope


def files(paths: list[str], context_dirs: list[str]) -> Scope:
    picked = read_tree(paths)
    keys = {os.path.normcase(os.path.abspath(c.path)) for c in picked}
    context = [c for c in read_tree(context_dirs) if os.path.normcase(os.path.abspath(c.path)) not in keys]
    return Scope("files", None, picked + context, picked, [c for c in picked if c.type in GRANT_TYPES],
                 outside=len(context))


def everything_under(paths: list[str]) -> Scope:
    comps = read_tree(paths)
    return Scope("all", None, comps, list(comps), [c for c in comps if c.type in GRANT_TYPES])
