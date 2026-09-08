"""snapshot_pre.py — pre-snapshot retrieve parser for sf 2.107.6 actual JSON shape.

Closes plan-v5 Closure 5 + Codex-R5-P1-4 (substring collision in fullName/type
matching can falsely classify present components as absent).

Codex empirically reproduced sf 2.107.6 retrieve JSON for missing component:
  result.status = 'Succeeded' (exit 0, even with missing components!)
  result.messages[0].fileName = 'unpackaged/package.xml'   (NOT 'file')
  result.messages[0].problem = "Entity of type 'ApexClass' named 'X' cannot be found"
  result.files[0].state = 'Failed'
  result.files[0].error = ... (sometimes; not always present)
  result.files[0] LACKS filePath entirely

Codex-R5-P1-4 fix: parse problem text with EXACT regex extracting
(type, fullName) tuple, match exactly. Substring matching ('Account' inside
'AccountManager') falsely triggers absent classification. Use regex.

Per-file classification:
  present: file actually retrieved + checksumable
  absent: result.files[].state='Failed' AND problem text matches not-found regex
  retrieve_failed: result.files[].state='Failed' AND problem doesn't match
                   not-found regex (some other failure)
  unknown: parser couldn't classify (raw_problem preserved for forensics)
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import NamedTuple


# Codex-R5-P1-4 fix: extract (type, fullName) as exact tuple, not substring match
_NOT_FOUND_PROBLEM_RE = re.compile(
    r"Entity of type '([^']+)' named '([^']+)' cannot be found"
)
# Older sf CLI versions may use different phrasings — keep these as fallback
# patterns that ALSO extract (type, fullName) tuples cleanly.
_NOT_FOUND_FALLBACK_PATTERNS = [
    re.compile(r"([^\s]+) named '([^']+)' cannot be found"),
    re.compile(r"([^\s]+) ([^\s]+) does not exist"),
    re.compile(r"([^\s]+) ([^\s]+) not found"),
]


class FileClassification(NamedTuple):
    type: str
    fullName: str
    state: str         # present | absent | retrieve_failed | unknown
    checksum: str | None  # sha256 hex if state=present
    file_path: str | None # disk path if state=present
    raw_problem: str | None  # original problem text if state in (absent, retrieve_failed, unknown)


class RetrieveResult(NamedTuple):
    status: str  # parsed result.status
    files: list[FileClassification]
    raw_json_path: Path  # path to persisted raw JSON for forensics


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_problem_tuple(problem: str) -> tuple[str, str] | None:
    """Extract (type, fullName) from problem text. Returns None if not a known
    not-found problem.

    Codex-R5-P1-4 fix: exact regex tuple extraction; never substring match.
    """
    m = _NOT_FOUND_PROBLEM_RE.search(problem)
    if m:
        return (m.group(1), m.group(2))
    for pat in _NOT_FOUND_FALLBACK_PATTERNS:
        m = pat.search(problem)
        if m:
            return (m.group(1), m.group(2))
    return None


def classify_retrieve_result(
    json_data: dict,
    output_dir: Path,
) -> list[FileClassification]:
    """Classify each requested component per sf 2.107.6 actual JSON shape.

    Args:
        json_data: parsed JSON output from `sf project retrieve start --json`
        output_dir: dir where sf wrote retrieved files
    Returns:
        List of FileClassification per file in result.files[]
    Raises:
        ValueError: if `result` key is missing (likely API change or auth error)
    """
    if "result" not in json_data:
        raise ValueError(
            "sf CLI JSON missing 'result' key — possible API shape change "
            "or auth error. Raw JSON top-level keys: " + str(list(json_data.keys())[:10])
        )

    result = json_data["result"]
    files = result.get("files")
    messages = result.get("messages", [])

    # Codex-R6-P1-4 fix: fail closed if 'files' key is missing entirely AND
    # we have not-found problems in messages. Returning [] silently was
    # fail-open — caller would record a snapshot with no component
    # classifications and proceed against an org that actually has missing
    # components. Either the API shape changed (refuse to assume) or sf
    # returned an unusual response shape we don't model (refuse to assume).
    if files is None:
        # Check whether messages look like real not-found problems
        suspicious_problems = [
            m.get("problem", "") for m in messages
            if m.get("problem") and _extract_problem_tuple(m.get("problem", "")) is not None
        ]
        if suspicious_problems:
            raise ValueError(
                f"sf CLI JSON has 'result.messages' with not-found problems "
                f"but missing 'result.files' key — refusing to assume state. "
                f"Possible API shape change. Sample problems: "
                f"{suspicious_problems[:2]}"
            )
        # No files key + no suspicious messages = empty retrieve OR empty selector.
        # This is acceptable; return empty list.
        files = []

    # Build a lookup: (type, fullName) → matching message problem text.
    # Codex-R5-P1-4 fix: this replaces the broken substring match.
    not_found_index: dict[tuple[str, str], str] = {}
    other_problems: list[str] = []
    for msg in messages:
        problem = msg.get("problem", "")
        if not problem:
            continue
        extracted = _extract_problem_tuple(problem)
        if extracted is not None:
            not_found_index[extracted] = problem
        else:
            other_problems.append(problem)

    classifications: list[FileClassification] = []
    for file_entry in files:
        ftype = file_entry.get("type", "")
        full_name = file_entry.get("fullName", "")
        state = file_entry.get("state", "")

        if state == "Failed":
            # Look up in the not-found index by EXACT (type, fullName) tuple
            problem = not_found_index.get((ftype, full_name))
            if problem is not None:
                classifications.append(FileClassification(
                    type=ftype, fullName=full_name,
                    state="absent", checksum=None, file_path=None,
                    raw_problem=problem,
                ))
                continue

            # Check files[].error if present (sf sometimes attaches per-file error)
            file_error = file_entry.get("error", "")
            if file_error:
                # Try to parse it as a not-found
                if _extract_problem_tuple(file_error) == (ftype, full_name):
                    classifications.append(FileClassification(
                        type=ftype, fullName=full_name,
                        state="absent", checksum=None, file_path=None,
                        raw_problem=file_error,
                    ))
                    continue

            # No matching not-found problem AND no parseable file error → unknown failure
            # Fail closed: classify as retrieve_failed (NOT absent — caller should
            # treat differently, e.g., retry or surface to operator)
            classifications.append(FileClassification(
                type=ftype, fullName=full_name,
                state="retrieve_failed", checksum=None, file_path=None,
                raw_problem=file_error or f"Failed state with no matching message problem (other_problems={other_problems[:3]})",
            ))
            continue

        # state in (Created, Changed, Unchanged) — verify file actually on disk
        file_path_str = file_entry.get("filePath")
        if file_path_str:
            file_path = output_dir / file_path_str
            if file_path.exists():
                classifications.append(FileClassification(
                    type=ftype, fullName=full_name,
                    state="present",
                    checksum=_sha256_file(file_path),
                    file_path=str(file_path),
                    raw_problem=None,
                ))
            else:
                classifications.append(FileClassification(
                    type=ftype, fullName=full_name,
                    state="retrieve_failed", checksum=None, file_path=None,
                    raw_problem=f"sf reported state={state} but file missing on disk: {file_path_str}",
                ))
        else:
            # No filePath — derive from conventions or fail closed
            derived = _derive_metadata_path(ftype, full_name, output_dir)
            if derived and derived.exists():
                classifications.append(FileClassification(
                    type=ftype, fullName=full_name,
                    state="present",
                    checksum=_sha256_file(derived),
                    file_path=str(derived),
                    raw_problem=None,
                ))
            else:
                classifications.append(FileClassification(
                    type=ftype, fullName=full_name,
                    state="retrieve_failed", checksum=None, file_path=None,
                    raw_problem=f"sf reported state={state} but no filePath provided and conventional path not found",
                ))

    return classifications


# Metadata path conventions. Used only when `sf` reports a component's state but
# gives no filePath; a type that is missing here derives no path, classifies
# `retrieve_failed`, and is therefore NOT captured — so the gap is silent and
# costs revertibility rather than raising an error.
#
# Extended 2026-07-28 from the original four ("Phase I.4 ships top 4; future
# phase extends"). CustomMetadata was the specific gap called out by the
# round-2 audit: application business logic can depend on CMDT records,
# so a CMDT deploy must be included in snapshot capture.
#
# EVERY pattern below was verified against a real file in `codebase/` or
# `standard-deployment/` rather than recalled — including the two awkward ones:
# a CustomMetadata fullName is already `<Type>.<Record>` and maps to ONE flat
# file (not a parent directory), and a Layout fullName legitimately contains
# hyphens and spaces ("ProductTransfer-Product Transfer Layout").
_PATH_CONVENTIONS: dict[str, tuple[str, str]] = {
    # type → (subdir, filename_pattern). {parent}/{name} come from splitting
    # fullName on the FIRST dot; {name} alone means the whole fullName.
    "ApexClass": ("classes", "{name}.cls"),
    "ApexTrigger": ("triggers", "{name}.trigger"),
    "Flow": ("flows", "{name}.flow-meta.xml"),
    "ValidationRule": ("objects/{parent}/validationRules", "{name}.validationRule-meta.xml"),
    # --- added 2026-07-28 ---
    "CustomMetadata": ("customMetadata", "{name}.md-meta.xml"),
    "CustomField": ("objects/{parent}/fields", "{name}.field-meta.xml"),
    "FieldSet": ("objects/{parent}/fieldSets", "{name}.fieldSet-meta.xml"),
    "RecordType": ("objects/{parent}/recordTypes", "{name}.recordType-meta.xml"),
    "CustomObject": ("objects/{name}", "{name}.object-meta.xml"),
    "PermissionSet": ("permissionsets", "{name}.permissionset-meta.xml"),
    "FlexiPage": ("flexipages", "{name}.flexipage-meta.xml"),
    "Layout": ("layouts", "{name}.layout-meta.xml"),
    "GlobalValueSet": ("globalValueSets", "{name}.globalValueSet-meta.xml"),
    "QuickAction": ("quickActions", "{name}.quickAction-meta.xml"),
    "CustomTab": ("tabs", "{name}.tab-meta.xml"),
    "CustomApplication": ("applications", "{name}.app-meta.xml"),
}


def _derive_metadata_path(ftype: str, full_name: str, output_dir: Path) -> Path | None:
    """Derive expected file path for a metadata component. Returns None if
    convention not known for this type.
    """
    convention = _PATH_CONVENTIONS.get(ftype)
    if convention is None:
        return None
    subdir_pattern, filename_pattern = convention
    # Three shapes, not two:
    #   {parent} in subdir  — ValidationRule/CustomField/FieldSet/RecordType:
    #                         fullName is `Object.Member`, split on the dot.
    #   {name}   in subdir  — CustomObject: the directory and the file share the
    #                         name (objects/Foo__c/Foo__c.object-meta.xml). This
    #                         shape had no branch at all before 2026-07-28, so
    #                         the subdir kept its literal "{name}" and the path
    #                         never existed.
    #   neither             — flat directory keyed by the whole fullName. Note
    #                         CustomMetadata lands HERE despite containing a
    #                         dot: `Type.Record` is one flat filename, so
    #                         splitting it would look up the wrong path.
    if "{parent}" in subdir_pattern:
        if "." not in full_name:
            return None
        parent, name = full_name.split(".", 1)
        subdir = subdir_pattern.format(parent=parent)
        filename = filename_pattern.format(name=name)
    elif "{name}" in subdir_pattern:
        subdir = subdir_pattern.format(name=full_name)
        filename = filename_pattern.format(name=full_name)
    else:
        subdir = subdir_pattern
        filename = filename_pattern.format(name=full_name)
    candidate = output_dir / "force-app" / "main" / "default" / subdir / filename
    if candidate.exists():
        return candidate
    # Try without force-app prefix (raw retrieve to flat dir)
    candidate = output_dir / subdir / filename
    if candidate.exists():
        return candidate
    return None


def run_pre_snapshot_retrieve(
    metadata_selectors: list[str],
    target_org: str,
    output_dir: Path,
    timeout_seconds: int = 180,
) -> RetrieveResult:
    """Execute `sf project retrieve start --json` and parse the result.

    Returns RetrieveResult with classified files + raw JSON path for forensics.

    STAGED INSIDE A THROWAWAY PROJECT — and this is load-bearing, not tidiness.

    `sf project retrieve start --output-dir` REFUSES any directory outside the
    current project root:

        OutputDirOutsideProjectError: The output directory must be inside the
        current project.

    The snapshot store is `<selected-client>/state/revert/<org>/<ts>-<id>/`, which
    is outside every SFDX project by construction. So passing it directly — which
    this function did until 2026-07-29 — could never succeed, for ANY org and ANY
    selector. Every `deploy_metadata` snapshot therefore captured nothing, and
    revert correctly but uselessly reported `automatic_revertible: false` on all
    of them. Proven on the throwaway org sf-deploy-test by changing exactly one
    variable:

        --output-dir INSIDE  the project -> status 0
        --output-dir OUTSIDE the project -> status 1 OutputDirOutsideProjectError

    sf resolves the project root from CWD, so the fix is to give it one: retrieve
    into a temp dir that contains a minimal sfdx-project.json, then move the tree
    into the snapshot store. `--target-metadata-dir` also escapes the constraint,
    but it returns a metadata-format `unpackaged.zip`, which would invalidate
    `_PATH_CONVENTIONS` and every source-format path derivation below. Staging
    keeps the retrieved shape identical to what the rest of this module already
    parses, so the blast radius of the fix is one function.
    """
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw_json_path = output_dir / ".retrieve-result.json"

    # .resolve() matters on macOS: /var is a symlink to /private/var, so an
    # unresolved temp path and sf's resolved project root compare unequal and it
    # rejects its OWN staging dir as "outside the project root". Belt and braces
    # with the relative --output-dir below, which avoids the comparison entirely.
    stage = Path(tempfile.mkdtemp(prefix="jsc-presnap-")).resolve()
    try:
        (stage / "sfdx-project.json").write_text(json.dumps(
            {"packageDirectories": [{"path": "force-app", "default": True}],
             "namespace": ""}))
        # The declared packageDirectories path must EXIST, not merely be named:
        # sf raises MissingPackageDirectoryError otherwise. Found the hard way —
        # the first version of this fix wrote the json without the directory and
        # traded OutputDirOutsideProjectError for MissingPackageDirectoryError.
        (stage / "force-app").mkdir(parents=True, exist_ok=True)
        staged_out = stage / "retrieved"

        cmd = ["sf", "project", "retrieve", "start",
               "--target-org", target_org,
               "--output-dir", "retrieved",       # RELATIVE to cwd=stage, on purpose
               "--json"]
        for sel in metadata_selectors:
            cmd.extend(["--metadata", sel])

        try:
            # cwd=stage is the whole point — it is what makes staged_out "inside
            # the project" as far as the sf CLI is concerned.
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout_seconds, cwd=str(stage))
        except subprocess.TimeoutExpired as e:
            raw_json_path.write_text(json.dumps({
                "error": "subprocess.TimeoutExpired",
                "stdout": e.stdout.decode() if e.stdout else "",
                "stderr": e.stderr.decode() if e.stderr else "",
            }, indent=2))
            raise

        # Move the retrieved tree into the snapshot store BEFORE classification,
        # so every path derived below resolves against output_dir exactly as it
        # did when the retrieve wrote there directly.
        if staged_out.is_dir():
            for item in staged_out.iterdir():
                dest = output_dir / item.name
                if dest.exists():
                    shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
                shutil.move(str(item), str(dest))
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    # Persist raw output regardless of exit
    raw_json_path.write_text(proc.stdout or "")

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"sf CLI returned non-JSON (exit {proc.returncode}). "
            f"Refusing to assume state. Raw stdout (first 200 chars): "
            f"{(proc.stdout or '')[:200]!r}, stderr: {(proc.stderr or '')[:200]!r}"
        ) from e

    # Re-point sf's ABSOLUTE filePath values from the (now-deleted) staging dir
    # to the snapshot store. Without this the files are captured correctly on
    # disk and then classified `retrieve_failed`, because classification does
    # `output_dir / file_path_str` — and pathlib returns the RIGHT-HAND side
    # unchanged when it is absolute, so it stats the staging path we just
    # removed. Observed exactly that on sf-deploy-test: metadata-before held a
    # correct CustomLabels file while payload.files said retrieve_failed.
    #
    # The raw .retrieve-result.json keeps sf's original paths on purpose — it is
    # the forensic record of what the CLI actually said, not a working index.
    staged_prefix = str(staged_out)
    for entry in (data.get("result", {}) or {}).get("files") or []:
        fp = entry.get("filePath")
        if fp and fp.startswith(staged_prefix):
            entry["filePath"] = str(output_dir / Path(fp).relative_to(staged_prefix))

    classifications = classify_retrieve_result(data, output_dir)
    # Preserve a capture-time inventory for compound metadata whose companion
    # files are not always enumerated individually in the CLI result.
    from .metadata_scope import write_capture_inventory
    write_capture_inventory(output_dir)
    status = data.get("result", {}).get("status", "Unknown")
    return RetrieveResult(status=status, files=classifications, raw_json_path=raw_json_path)
