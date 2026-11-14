"""Run Salesforce Code Analyzer on the in-scope flow, Apex and component files and merge its findings.

Through the existing adapter (`jsc_loganalyzer.code_analyzer`). The run uses the rule selectors
`Recommended` and `flow`: the flow engine's MissingFaultHandler, MissingDescription,
UnreachableElement and UnusedResource rules are outside the default run. Coverage is claimed only
for a run that wrote its report.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
import shutil
import tempfile

from .components import Component
from .model import RANK, Finding

SELECTORS = ("Recommended", "flow")
ANALYZED = ("Flow", "ApexClass", "ApexTrigger", "LightningComponentBundle", "AuraDefinitionBundle")
# The analyzer rule -> Torque's own rule ids for the same problem. When both report it on the same file and
# element, it is reported once, as Torque's finding, with the analyzer named in confirmed_by.
SAME_AS = {
    ("flow", "MissingFaultHandler"): ("flow.no-fault-path",),
    ("flow", "HardcodedId"): ("flow.hard-coded-id",),
    ("flow", "DbInLoop"): ("flow.work-in-loop",),
    ("flow", "TriggerEntryCriteria"): ("flow.no-entry-conditions",),
    ("flow", "DefaultCopy"): ("flow.default-names",),
    ("flow", "SameRecordUpdate"): ("flow.after-save-same-record",),
    ("flow", "MissingDescription"): ("doc.description-missing", "doc.flow-elements-undescribed"),
    ("regex", "AvoidOldSalesforceApiVersions"): ("flow.api-version-behind",),
    ("pmd", "ApexSharingViolations"): ("code.no-sharing-declaration",),
    ("pmd", "ApexUnitTestClassShouldHaveAsserts"): ("code.test-without-assert",),
    ("pmd", "ApexUnitTestShouldNotUseSeeAllDataTrue"): ("code.see-all-data",),
}
FILE_LEVEL = ("code.",)   # Apex rules: one per class, so the same file is enough


@dataclass
class AnalyzerRun:
    mode: str
    status: str = "off"            # ran, not-installed, failed, off, not-applicable
    detail: str = ""
    targets: int = 0
    violations: int = 0
    merged: int = 0
    versions: dict = field(default_factory=dict)

    def line(self) -> str:
        if self.status == "ran":
            return (f"Code Analyzer: ran with rule selectors {' + '.join(SELECTORS)} on {self.targets} file(s): "
                    f"{self.violations} finding(s), {self.merged} of them also found by Torque and reported once.")
        if self.status == "not-installed":
            return f"Code Analyzer: not installed, so its rules did not run ({self.detail})."
        if self.status == "failed":
            return f"Code Analyzer: did not run ({self.detail}); its rules are not covered."
        if self.status == "not-applicable":
            return "Code Analyzer: nothing in scope for it (no flow, Apex or Lightning component files)."
        return "Code Analyzer: off (--analyzer off); its rules did not run."


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _workspace(project_root: str | None, targets: list[str]) -> str:
    if project_root and all(_norm(t).startswith(_norm(project_root) + os.sep) for t in targets):
        return project_root
    common = os.path.commonpath([os.path.abspath(t) for t in targets])
    return common if os.path.isdir(common) else os.path.dirname(common)


def run(mode: str, in_scope: list[Component], project_root: str | None, findings: list[Finding],
        ignored_rules: set[str], runner=None) -> tuple[AnalyzerRun, list[Finding]]:
    """(the run's summary, analyzer findings not already reported). `runner` replaces the adapter in tests."""
    result = AnalyzerRun(mode)
    if mode == "off":
        return result, []
    comps = [c for c in in_scope if c.type in ANALYZED and not c.error]
    if not comps:
        result.status = "not-applicable"
        return result, []
    targets = sorted({c.path for c in comps})
    result.targets = len(targets)
    workspace = _workspace(project_root, targets)
    try:
        from jsc_loganalyzer import code_analyzer as adapter
    except ImportError as exc:
        result.status, result.detail = "failed", f"the bundled adapter could not be imported: {exc.name}"
        return result, []
    runner = runner or adapter.run_code_analyzer
    folder = tempfile.mkdtemp(prefix="torque-check-")
    env = dict(os.environ)
    env.setdefault("SF_DISABLE_TELEMETRY", "true")
    try:
        report = runner(workspace, os.path.join(folder, "code-analyzer.json"), targets=targets,
                        rule_selectors=SELECTORS, cwd=workspace, env=env)
    except adapter.NotInstalled as exc:
        result.status, result.detail = "not-installed", str(exc).rstrip(".")
        return result, []
    except (RuntimeError, OSError, ValueError) as exc:
        result.status, result.detail = "failed", " ".join(str(exc).split())[:300]
        return result, []
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    result.status = "ran"
    result.versions = report.get("versions") if isinstance(report.get("versions"), dict) else {}
    run_dir = report.get("runDir") or workspace
    by_path = {_norm(c.path): c for c in comps}
    bundle_dirs = [(_norm(c.path), c) for c in comps if c.type.endswith("Bundle")]
    out: list[Finding] = []
    for v in report.get("violations") or []:
        if not isinstance(v, dict):
            continue
        locations = v.get("locations") or [{}]
        idx = v.get("primaryLocationIndex", 0)
        loc = locations[idx] if isinstance(idx, int) and 0 <= idx < len(locations) else locations[0]
        file = str(loc.get("file") or "")
        path = _norm(file if os.path.isabs(file) else os.path.join(run_dir, file))
        comp = by_path.get(path) or next((c for d, c in bundle_dirs if path.startswith(d + os.sep)), None)
        if comp is None:
            continue   # outside the change
        result.violations += 1
        engine, rule = str(v.get("engine") or "?"), str(v.get("rule") or "?")
        line = loc.get("startLine") if isinstance(loc.get("startLine"), int) else None
        own = SAME_AS.get((engine, rule), ())
        if any(r in ignored_rules for r in own):
            continue
        severity = v.get("severity") if isinstance(v.get("severity"), int) else 5
        level = "error" if severity <= 2 else "warning" if severity == 3 else "note"
        twin = next((f for f in findings if f.rule in own and _norm(f.path) == path
                     and (f.rule.startswith(FILE_LEVEL) or line in (f.match_lines or {f.line}))), None)
        if twin is not None:
            # One finding shown, at the higher of the two severities, so --fail-on still sees the analyzer's.
            if RANK[level] < RANK[twin.severity]:
                twin.severity = level
                twin.confirmed_by.append(f"code-analyzer {engine}:{rule} (severity {severity}, so {level})")
            else:
                twin.confirmed_by.append(f"code-analyzer {engine}:{rule}")
            result.merged += 1
            continue
        resources = [r for r in v.get("resources") or [] if isinstance(r, str)]
        out.append(Finding(f"code-analyzer:{engine}:{rule}", level, comp,
                           " ".join(str(v.get("message") or rule).split()),
                           "Fix it as the analyzer's rule describes" + (f" ({resources[0]})." if resources else "."),
                           line=line, file=path, source="code-analyzer", docs=resources[0] if resources else ""))
    return result, out
