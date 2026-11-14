"""`torque check metadata`: the whole run, returning one JSON-ready report (schema in docs/checks.md)."""
from __future__ import annotations

from collections import Counter
import os
from pathlib import Path

from . import analyzer, config, rules, scope as scopes
from .components import find_project
from .model import Finding

SCHEMA_VERSION = 1


class InputError(ValueError):
    """Unusable input: exit 2."""


def _rel(path: str, base: str) -> str:
    try:
        rel = os.path.relpath(path, base)
    except ValueError:
        return Path(path).as_posix()
    return Path(path).as_posix() if rel.startswith("..") else Path(rel).as_posix()


def _lessons(types: list[str], paths: list[str]) -> str | None:
    """Notes from earlier work for these metadata types, when the lessons package offers them."""
    try:
        from jsc_memory import recall
        text = recall.for_command("torque check metadata", types=tuple(types), tasks=("deploy", "check"),
                                  paths=tuple(paths[:50]), via="check-metadata")
    except Exception:  # noqa: BLE001 - lessons are optional and must never fail the check
        return None
    if not isinstance(text, str):
        return None
    return text.strip() or None


def check(paths: list[str], *, changed_from: str | None = None, baseline: str | None = None,
          only: list[str] | None = None, analyzer_mode: str = "auto", ignore: list[str] | None = None,
          runner=None, lessons: bool = True) -> dict:
    paths = paths or ["."]
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        raise InputError(f"no such file or folder: {', '.join(missing)}")
    unknown = [r for r in ignore or [] if r not in rules.RULES and not r.startswith("code-analyzer:")]
    if unknown:
        raise InputError(f"--ignore: unknown rule {', '.join(unknown)}; `torque check metadata --help` lists the rules")
    project = find_project(paths)
    try:
        cfg = config.load(config.find(paths, project.root), rules.RULES)
        if changed_from:
            sc = scopes.changed_from(paths, changed_from)
        elif baseline:
            sc = scopes.with_baseline(paths, baseline)
        elif only:
            sc = scopes.only(paths, only)
        elif any(os.path.isfile(p) for p in paths):
            # Every file and folder given is in scope; the project around them is read for context only.
            sc = scopes.files(paths, [project.root] if project.root else [])
        else:
            sc = scopes.everything_under(paths)
    except (scopes.ScopeError, config.ConfigError, scopes.deploy_result.ResultError) as exc:
        raise InputError(str(exc)) from None

    ignored_components = [c for c in sc.in_scope if cfg.path_ignored(c.path)]
    in_scope = [c for c in sc.in_scope if c not in ignored_components]
    ctx = rules.build_context(project, sc.everything, in_scope, sc.grant_scope, sc.baseline)
    ctx.scope_mode = sc.mode
    if sc.mode == "all":
        ctx.scope_word = "in scope"
    found = rules.run(in_scope, ctx)
    dropped = Counter()
    ignored = set(ignore or []) | cfg.rules_everywhere()
    kept: list[Finding] = []
    for f in found:
        if f.rule in ignored or cfg.rule_ignored(f.rule, f.path):
            dropped[f.rule] += 1
        else:
            kept.append(f)
    run, extra = analyzer.run(analyzer_mode, in_scope, project.root, kept, ignored, runner=runner)
    for f in extra:
        if f.rule in ignored or cfg.rule_ignored(f.rule, f.path):
            dropped[f.rule] += 1
        else:
            kept.append(f)
    kept = rules.order(kept)
    base = project.root or os.path.abspath(paths[0] if os.path.isdir(paths[0]) else os.path.dirname(paths[0]) or ".")
    types = sorted({c.type for c in in_scope})
    return {
        "schema_version": SCHEMA_VERSION,
        "command": "torque check metadata",
        "base": Path(base).as_posix(),
        "scope": _scope(sc, in_scope, ignored_components, paths, base),
        "analyzer": {"mode": analyzer_mode, "status": run.status, "detail": run.detail,
                     "rule_selectors": list(analyzer.SELECTORS) if run.status == "ran" else [],
                     "targets": run.targets, "findings": run.violations, "reported_once_with_torque": run.merged,
                     "versions": run.versions, "summary": run.line()},
        "findings": [_finding(f, base) for f in kept],
        "totals": _totals(kept),
        "ignored": {"config": Path(cfg.path).as_posix() if cfg.path else None, "rules": sorted(ignored),
                    "components": len(ignored_components), "findings_dropped": dict(sorted(dropped.items()))},
        "not_checked": _not_checked(sc, ctx, run),
        "lessons": _lessons(types, [c.path for c in in_scope]) if lessons else None,
    }


def _scope(sc, in_scope, ignored_components, paths, base) -> dict:
    counts = Counter(c.status for c in in_scope)
    return {
        "mode": sc.mode, "source": Path(sc.source).as_posix() if sc.source else None,
        "paths": [Path(os.path.abspath(p)).as_posix() for p in paths],
        "components_read": len(sc.everything), "files_read": len({c.path for c in sc.everything}),
        "in_scope": len(in_scope), "by_status": dict(sorted(counts.items())),
        "unchanged_not_checked": sc.unchanged, "outside_the_change": sc.outside,
        "ignored_by_config": len(ignored_components),
        "not_found": sc.not_found, "deleted": sc.deleted,
        "types_without_rules": dict(sorted(sc.unsupported.items())),
        "unreadable_files": sum(1 for c in in_scope if c.error),
        "deploy_result": ({"id": sc.result.id, "status": sc.result.status, "check_only": sc.result.check_only}
                          if sc.result else None),
    }


def _finding(f: Finding, base: str) -> dict:
    return {"rule": f.rule, "severity": f.severity, "source": f.source,
            "component": {"type": f.comp.type, "name": f.comp.name, "status": f.comp.status,
                          **({"state": f.comp.state} if f.comp.state else {})},
            "file": _rel(f.path, base), "line": f.line, "message": f.message, "action": f.action,
            "inherited": f.inherited, "confirmed_by": f.confirmed_by, **({"docs": f.docs} if f.docs else {})}


def _totals(findings: list[Finding]) -> dict:
    c = Counter(f.severity for f in findings)
    return {"error": c.get("error", 0), "warning": c.get("warning", 0), "note": c.get("note", 0),
            "inherited": sum(1 for f in findings if f.inherited), "components": len({id(f.comp) for f in findings})}


def _not_checked(sc, ctx, run) -> list[str]:
    out = []
    if sc.unchanged:
        out.append(f"{sc.unchanged} unchanged component(s)")
    if sc.not_found:
        out.append(f"{len(sc.not_found)} component(s) named in the scope with no file under the checked folder")
    if sc.unsupported:
        out.append("component types with no rules here: " + ", ".join(f"{t} ({n})" for t, n in sorted(sc.unsupported.items())))
    if sc.deleted:
        out.append(f"{len(sc.deleted)} deleted component(s)")
    if sc.mode != "baseline":
        out.append("lost page sections (needs --baseline)")
    if sc.mode == "changed-from" and any(c.status == "changed" for c in sc.in_scope):
        out.append("which problems on changed components were already there (needs --baseline)")
    out.append("grants made in Setup or as data records, assignments and what users can really see "
               "(`torque check deployed` reads the org's grants)")
    out.append("flow activation in the org (`torque check deployed`)")
    out.append("whether descriptions and help text are right, only that they are there")
    if run.status not in ("ran", "not-applicable"):
        out.append("Code Analyzer's rules")
    return out + ctx.not_checked
