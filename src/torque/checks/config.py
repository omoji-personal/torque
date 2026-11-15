"""`torque-check.yml`: rules or paths a project ignores, each with its reason.

    ignore:
      - rule: doc.help-text-missing
        reason: help text is managed on the page layouts
      - path: force-app/main/default/classes/legacy/*
        reason: legacy code outside this engagement
      - rule: code.test-without-assert
        path: "*/AssertHelperTest.cls"
        reason: assertions live in the helper

A path is matched against the file's path relative to the folder holding the file, with forward
slashes; `*` also matches across folders.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
import os
from pathlib import Path

NAME = "torque-check.yml"


class ConfigError(ValueError):
    pass


@dataclass
class Ignore:
    rule: str | None
    path: str | None
    reason: str


@dataclass
class Config:
    path: str | None = None
    entries: list[Ignore] = field(default_factory=list)

    @property
    def folder(self) -> str:
        return os.path.dirname(self.path) if self.path else ""

    def _rel(self, file: str) -> str:
        try:
            return Path(os.path.relpath(file, self.folder)).as_posix()
        except ValueError:      # another drive on Windows
            return Path(file).as_posix()

    def path_ignored(self, file: str) -> Ignore | None:
        rel = self._rel(file).lower()
        return next((e for e in self.entries if e.rule is None and e.path and fnmatchcase(rel, e.path.lower())), None)

    def rule_ignored(self, rule: str, file: str) -> Ignore | None:
        rel = self._rel(file).lower()
        for e in self.entries:
            if e.rule == rule and (e.path is None or fnmatchcase(rel, e.path.lower())):
                return e
        return None

    def rules_everywhere(self) -> set[str]:
        return {e.rule for e in self.entries if e.rule and e.path is None}


def find(paths: list[str], project_root: str | None) -> str | None:
    here = Path(os.path.abspath(paths[0] if paths else "."))
    if here.is_file():
        here = here.parent
    stop = Path(project_root) if project_root else here
    for folder in (here, *here.parents):
        if (folder / NAME).is_file():
            return str(folder / NAME)
        if folder == stop:
            break
    return None


def load(path: str | None, known_rules) -> Config:
    if path is None:
        return Config()
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - PyYAML is a Torque dependency
        raise ConfigError(f"{path}: PyYAML is needed to read it ({exc})") from None
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8-sig")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"{path} could not be read: {' '.join(str(exc).split())[:200]}") from None
    if not isinstance(data, dict) or not isinstance(data.get("ignore", []), list) or set(data) - {"ignore"}:
        raise ConfigError(f"{path}: the file holds one key, ignore, with a list of entries (rule, path, reason)")
    entries = []
    for i, item in enumerate(data.get("ignore") or [], 1):
        if not isinstance(item, dict) or set(item) - {"rule", "path", "reason"}:
            raise ConfigError(f"{path}: ignore entry {i} must have rule and/or path, and reason")
        rule, where, reason = item.get("rule"), item.get("path"), str(item.get("reason") or "").strip()
        if not reason:
            raise ConfigError(f"{path}: ignore entry {i} has no reason; say why the finding does not apply")
        if not rule and not where:
            raise ConfigError(f"{path}: ignore entry {i} names neither a rule nor a path")
        if rule and rule not in known_rules and not str(rule).startswith("code-analyzer:"):
            raise ConfigError(f"{path}: ignore entry {i} names an unknown rule {rule!r}; "
                              "`torque check metadata --help` lists the rules")
        entries.append(Ignore(str(rule) if rule else None, str(where) if where else None, reason))
    return Config(path, entries)
