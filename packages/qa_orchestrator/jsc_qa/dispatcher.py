"""dispatcher.py — invoke verification surfaces.

Each surface gets a dispatcher function that:
  - Knows the invocation pattern (slash command, hook, manual)
  - Returns a structured DispatchResult
  - Honors target-org parameterization + timeouts (per Codex-R1-P3-1)

Production-status surfaces (per qa-router.yaml): MetaAPI, Side-Eff, Parity,
Funct-Pl, Funct-MCP, Multi-Prof, Adv-Probe, Hook-Gate, TAA, Hostile-QA,
Vision. Phase-2-WIP / deferred: AI-Prompt (scaffolding parked pending
unified JsonExtractor maturation), a11y, Visual-Reg.

Ship history:
- Funct-Pl + Multi-Prof: production 2026-05-14 (v7.14.0)
- Vision: production 2026-05-15 (v7.15.0)
- Adv-Probe: production 2026-05-15 (v7.16.0 — jsc_probes static synthesis)
- AI-Prompt: scaffolding-only 2026-05-15 (v7.15.1, parked phase_2_wip)
"""

from __future__ import annotations
from jsc_common.workspace import workspace_root, state_dir
import sys

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any

# B1: consolidated production/sandbox classification. Benign sys.path bootstrap
# so a bare `PYTHONPATH=packages/qa_orchestrator` invocation still finds
# packages/jsc_common (run-all-tests.sh + install-packages.sh also put it on
# path). QA consumers may keep this best-effort try/except; the two STRING-ONLY
# hooks must NOT (they fail loudly per spec).
try:
    from jsc_common.org_classify import is_production_target
except ImportError:  # pragma: no cover - bootstrap for bare PYTHONPATH
    import sys as _sys
    _common = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "jsc_common",
    )
    if _common not in _sys.path:
        _sys.path.insert(0, _common)
    from jsc_common.org_classify import is_production_target


@dataclass
class DispatchResult:
    """Result of invoking a single verification surface against a change."""
    surface: str
    status: str  # 'PASS' | 'FAIL' | 'MANUAL_REQUIRED' | 'DEFERRED' | 'SKIP_VIA_TOKEN' | 'ERROR'
    detail: str
    duration_seconds: float = 0.0
    invocation_command: str = ""
    raw_output: str = ""
    metadata: dict = field(default_factory=dict)


# Subprocess timeouts per Codex-R1-P3-1
TIMEOUT_FAST = 30      # SOQL query
TIMEOUT_MEDIUM = 180   # deploy retrieve, log analysis
TIMEOUT_LONG = 600     # full flow walkthrough


_DEPLOY_JOB_ID_RE = re.compile(
    r"0Af[A-Za-z0-9]{12}(?:[A-Z0-5]{3})?", re.ASCII
)
_SALESFORCE_ID_CHECKSUM_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"


def _canonical_deploy_job_id(value: Any) -> str | None:
    """Validate a DeployRequest ID and return its case-sensitive 15 characters.

    Salesforce may return the 18-character representation of a submitted
    15-character ID. Never truncate an 18-character value before checking its
    suffix against the capitalization of all three five-character blocks.
    """
    if not isinstance(value, str) or not _DEPLOY_JOB_ID_RE.fullmatch(value):
        return None
    base = value[:15]
    if len(value) == 18:
        suffix = "".join(
            _SALESFORCE_ID_CHECKSUM_ALPHABET[
                sum(1 << bit for bit, character in enumerate(base[start:start + 5])
                    if "A" <= character <= "Z")
            ]
            for start in (0, 5, 10)
        )
        if value[15:] != suffix:
            return None
    return base


_META_API_REPORT_API_VERSION = "65.0"
_META_API_PENDING_STATUSES = {
    "pending", "queued", "inprogress", "in_progress", "canceling",
}
_META_API_FAILURE_STATUSES = {
    "failed", "canceled", "cancelled", "error",
}
_COMPONENT_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_MANIFEST_API_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")


def _parse_expected_components(
    deploy_components: list[str] | tuple[str, ...],
) -> tuple[set[tuple[str, str]], str | None]:
    """Parse repeatable TYPE:FULL_NAME values into an exact, de-duplicated set."""
    if not isinstance(deploy_components, (list, tuple)):
        return set(), "deploy components must be supplied as a repeatable list"

    parsed: set[tuple[str, str]] = set()
    for index, raw_component in enumerate(deploy_components, start=1):
        if not isinstance(raw_component, str):
            return set(), f"deploy component #{index} must be a string"
        component_type, separator, full_name = raw_component.partition(":")
        component_type = component_type.strip()
        full_name = full_name.strip()
        if (
            not separator
            or not component_type
            or not full_name
            or not _COMPONENT_TYPE_RE.fullmatch(component_type)
            or any(character in "\r\n\t" for character in raw_component)
        ):
            return set(), (
                f"deploy component #{index} must use TYPE:FULL_NAME with a "
                "Salesforce metadata type and non-empty full name"
            )
        parsed.add((component_type, full_name))
    return parsed, None


def _parse_deploy_manifest(
    deploy_manifest: str,
) -> tuple[set[tuple[str, str]], str | None, str | None]:
    """Parse a local package.xml into exact pairs and its byte SHA-256."""
    import hashlib
    from pathlib import Path
    import xml.etree.ElementTree as ElementTree

    if not isinstance(deploy_manifest, str) or not deploy_manifest.strip():
        return set(), None, "deploy manifest path must be a non-empty string"

    manifest_path = Path(deploy_manifest).expanduser()
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as exc:
        return set(), None, (
            f"deploy manifest could not be read ({type(exc).__name__})"
        )
    if not manifest_bytes:
        return set(), None, "deploy manifest is empty"
    if b"<!DOCTYPE" in manifest_bytes.upper():
        return set(), None, "deploy manifest must not contain a DOCTYPE"

    digest = "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
    try:
        root = ElementTree.fromstring(manifest_bytes)
    except ElementTree.ParseError as exc:
        return set(), None, f"deploy manifest XML is malformed ({exc})"

    def local_name(tag: Any) -> str:
        return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""

    if local_name(root.tag) != "Package":
        return set(), None, "deploy manifest root must be Package"

    children = list(root)
    version_nodes = [node for node in children if local_name(node.tag) == "version"]
    if (
        len(version_nodes) != 1
        or not isinstance(version_nodes[0].text, str)
        or not _MANIFEST_API_VERSION_RE.fullmatch(version_nodes[0].text.strip())
    ):
        return set(), None, "deploy manifest must contain one numeric version"

    parsed: set[tuple[str, str]] = set()
    type_nodes = [node for node in children if local_name(node.tag) == "types"]
    for index, type_node in enumerate(type_nodes, start=1):
        type_children = list(type_node)
        name_nodes = [
            node for node in type_children if local_name(node.tag) == "name"
        ]
        member_nodes = [
            node for node in type_children if local_name(node.tag) == "members"
        ]
        if (
            len(name_nodes) != 1
            or not isinstance(name_nodes[0].text, str)
            or not _COMPONENT_TYPE_RE.fullmatch(name_nodes[0].text.strip())
        ):
            return set(), None, (
                f"deploy manifest types block #{index} has an invalid name"
            )
        if not member_nodes:
            return set(), None, (
                f"deploy manifest types block #{index} has no members"
            )

        component_type = name_nodes[0].text.strip()
        for member_index, member_node in enumerate(member_nodes, start=1):
            full_name = (
                member_node.text.strip()
                if isinstance(member_node.text, str)
                else ""
            )
            if (
                not full_name
                or "*" in full_name
                or any(character in "\r\n\t" for character in full_name)
            ):
                return set(), None, (
                    f"deploy manifest types block #{index} member "
                    f"#{member_index} is empty, wildcarded, or malformed"
                )
            parsed.add((component_type, full_name))

    if not parsed:
        return set(), None, "deploy manifest contains no exact components"
    return parsed, digest, None


def _is_package_xml_component(entry: dict[str, Any]) -> bool:
    """Identify the package.xml pseudo-entry emitted in componentSuccesses."""
    for key in ("fullName", "fileName"):
        value = entry.get(key)
        if isinstance(value, str):
            basename = value.replace("\\", "/").rsplit("/", 1)[-1]
            if basename.casefold() == "package.xml":
                return True
    return False


def _extract_reported_components(
    result: dict[str, Any],
) -> tuple[
    set[tuple[str, str]] | None,
    set[tuple[str, str]] | None,
    set[tuple[str, str]] | None,
    int,
    str | None,
]:
    """Extract exact component evidence from a successful deploy report.

    Returns all reported components, successful components, unsuccessful
    components, excluded package.xml count, and an error string. Component
    names are kept internal; callers expose only sanitized counts.
    """
    details = result.get("details")
    if not isinstance(details, dict):
        return None, None, None, 0, "result.details is missing or malformed"
    entries = details.get("componentSuccesses")
    if not isinstance(entries, list):
        return None, None, None, 0, (
            "result.details.componentSuccesses is missing or malformed"
        )

    reported: set[tuple[str, str]] = set()
    successful: set[tuple[str, str]] = set()
    unsuccessful: set[tuple[str, str]] = set()
    excluded_package_entries = 0
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            return None, None, None, excluded_package_entries, (
                f"componentSuccesses[{index}] is not an object"
            )
        if _is_package_xml_component(entry):
            excluded_package_entries += 1
            continue

        component_type = entry.get("componentType")
        full_name = entry.get("fullName")
        component_success = entry.get("success")
        if (
            not isinstance(component_type, str)
            or not component_type
            or not isinstance(full_name, str)
            or not full_name
            or not isinstance(component_success, bool)
        ):
            return None, None, None, excluded_package_entries, (
                f"componentSuccesses[{index}] lacks typed componentType, "
                "fullName, or success evidence"
            )

        component = (component_type, full_name)
        reported.add(component)
        if component_success:
            successful.add(component)
        else:
            unsuccessful.add(component)

    if not reported:
        return None, None, None, excluded_package_entries, (
            "componentSuccesses contained no deploy component evidence after "
            "excluding package.xml"
        )
    return reported, successful, unsuccessful, excluded_package_entries, None


def _meta_api_target_mismatch(
    payload: dict[str, Any], result: dict[str, Any], target_org: str,
) -> tuple[str, str] | None:
    """Return an explicit target mismatch when the report exposes comparable data.

    The report is fetched directly from the selected org's REST endpoint;
    unlike project deploy report, this transport does not choose a cached org.
    When the response also exposes an alias, username, or org ID, compare
    like-for-like and reject contradictory evidence.
    """
    containers = (payload, result)
    requested = target_org.casefold()

    if "@" in target_org:
        for container in containers:
            for key in ("targetUsername", "username"):
                value = container.get(key)
                if isinstance(value, str) and value.casefold() != requested:
                    return key, value
    elif target_org.startswith("00D") and len(target_org) in (15, 18):
        for container in containers:
            for key in ("targetOrgId", "orgId"):
                value = container.get(key)
                if isinstance(value, str) and value.casefold() != requested:
                    return key, value
    else:
        for container in containers:
            for key in ("targetOrgAlias", "targetAlias"):
                value = container.get(key)
                if isinstance(value, str) and value.casefold() != requested:
                    return key, value
    return None


def dispatch_meta_api(
    target_org: str,
    change_description: str,
    *,
    deploy_job_id: str | None = None,
    deploy_components: list[str] | tuple[str, ...] | None = None,
    deploy_manifest: str | None = None,
) -> DispatchResult:
    """Verify one exact Metadata API deploy or validation request.

    PASS is deliberately narrow: the operator must supply a Salesforce deploy
    request ID and at least one expected artifact, supplied explicitly and/or
    through a local package.xml. The report must echo the same valid ID (either
    its 15- or checksum-verified 18-character representation), include
    every expected artifact, and be terminal and successful. This surface never
    falls back to a cached "most recent" deployment because that can certify an
    unrelated change.
    """
    import shlex
    import time

    t0 = time.monotonic()
    if (
        not isinstance(target_org, str)
        or not target_org
        or target_org != target_org.strip()
        or any(character.isspace() for character in target_org)
    ):
        return DispatchResult(
            surface="MetaAPI", status="ERROR",
            detail=(
                f"Invalid target org {target_org!r}; supply one non-empty "
                "Salesforce alias, username, or org ID without whitespace."
            ),
            duration_seconds=time.monotonic() - t0,
            invocation_command="(invalid target org)",
            metadata={"target_org": target_org, "requested_job_id": deploy_job_id},
        )

    explicit_components_missing = (
        deploy_components is None
        or deploy_components == []
        or deploy_components == ()
    )
    manifest_missing = deploy_manifest is None or deploy_manifest == ""

    explicit_components: set[tuple[str, str]] = set()
    if not explicit_components_missing:
        explicit_components, component_input_error = _parse_expected_components(
            deploy_components
        )
        if component_input_error:
            return DispatchResult(
                surface="MetaAPI", status="ERROR",
                detail=(
                    "Invalid expected deploy component evidence: "
                    f"{component_input_error}."
                ),
                duration_seconds=time.monotonic() - t0,
                invocation_command="(invalid deploy component evidence)",
                metadata={
                    "target_org": target_org,
                    "requested_job_id": deploy_job_id,
                    "explicit_component_count": 0,
                    "manifest_component_count": 0,
                    "expected_component_count": 0,
                    "expected_components": [],
                    "reported_component_count": None,
                    "deploy_manifest_sha256": None,
                },
            )

    manifest_components: set[tuple[str, str]] = set()
    manifest_sha256 = None
    if not manifest_missing:
        (
            manifest_components,
            manifest_sha256,
            manifest_input_error,
        ) = _parse_deploy_manifest(deploy_manifest)
        if manifest_input_error:
            return DispatchResult(
                surface="MetaAPI", status="ERROR",
                detail=(
                    "Invalid deploy manifest evidence: "
                    f"{manifest_input_error}."
                ),
                duration_seconds=time.monotonic() - t0,
                invocation_command="(invalid deploy manifest evidence)",
                metadata={
                    "target_org": target_org,
                    "requested_job_id": deploy_job_id,
                    "explicit_component_count": len(explicit_components),
                    "manifest_component_count": 0,
                    "expected_component_count": len(explicit_components),
                    "expected_components": sorted(f"{kind}:{name}" for kind, name in explicit_components),
                    "reported_component_count": None,
                    "deploy_manifest_sha256": None,
                },
            )

    expected_components = explicit_components | manifest_components
    manifest_hash_detail = (
        f", manifest_sha256={manifest_sha256}" if manifest_sha256 else ""
    )
    missing_requirements = []
    if deploy_job_id is None or deploy_job_id == "":
        missing_requirements.append("--deploy-job-id <0Af...>")
    if explicit_components_missing and manifest_missing:
        missing_requirements.append(
            "at least one --deploy-component TYPE:FULL_NAME or "
            "--deploy-manifest package.xml"
        )
    if missing_requirements:
        return DispatchResult(
            surface="MetaAPI", status="MANUAL_REQUIRED",
            detail=(
                "Exact deploy and artifact evidence is required; missing: "
                + ", ".join(missing_requirements)
                + ". MetaAPI will not inspect the most recent deployment or "
                "infer its artifact scope."
            ),
            duration_seconds=time.monotonic() - t0,
            invocation_command="(awaiting exact deploy evidence)",
            metadata={
                "target_org": target_org,
                "requested_job_id": deploy_job_id or None,
                "explicit_component_count": len(explicit_components),
                "manifest_component_count": len(manifest_components),
                "expected_component_count": len(expected_components),
                "expected_components": sorted(f"{kind}:{name}" for kind, name in expected_components),
                "reported_component_count": None,
                "deploy_manifest_sha256": manifest_sha256,
            },
        )

    requested_job_identity = _canonical_deploy_job_id(deploy_job_id)
    if requested_job_identity is None:
        return DispatchResult(
            surface="MetaAPI", status="ERROR",
            detail=(
                f"Invalid deploy job ID {deploy_job_id!r}; expected a 15- or "
                "18-character ASCII Salesforce DeployRequest ID beginning with '0Af'; "
                "18-character IDs must have a valid uppercase checksum suffix."
            ),
            duration_seconds=time.monotonic() - t0,
            invocation_command="(invalid deploy job ID)",
            metadata={
                "target_org": target_org,
                "requested_job_id": deploy_job_id,
                "explicit_component_count": len(explicit_components),
                "manifest_component_count": len(manifest_components),
                "expected_component_count": len(expected_components),
                "expected_components": sorted(f"{kind}:{name}" for kind, name in expected_components),
                "reported_component_count": None,
                "deploy_manifest_sha256": manifest_sha256,
            },
        )

    cmd = [
        "sf", "api", "request", "rest",
        f"/services/data/v{_META_API_REPORT_API_VERSION}/metadata/"
        f"deployRequest/{deploy_job_id}?includeDetails=true",
        "--method", "GET",
        "--target-org", target_org,
        "--json",
    ]
    invocation = shlex.join(cmd)
    base_metadata = {
        "target_org": target_org,
        "requested_job_id": deploy_job_id,
        "explicit_component_count": len(explicit_components),
        "manifest_component_count": len(manifest_components),
        "expected_component_count": len(expected_components),
        "expected_components": sorted(f"{kind}:{name}" for kind, name in expected_components),
        "reported_component_count": None,
        "deploy_manifest_sha256": manifest_sha256,
        # The direct REST command uses the explicit org, with no deployment
        # cache fallback. The existing evidence schema remains unchanged.
        "target_evidence": "sf_target_org_scope",
        "report_transport": "sf_api_request_rest",
        "report_api_version": _META_API_REPORT_API_VERSION,
    }
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_MEDIUM)
    except (subprocess.TimeoutExpired, OSError) as e:
        return DispatchResult(
            surface="MetaAPI", status="ERROR",
            detail=f"Could not collect exact deploy report evidence: {e}",
            duration_seconds=time.monotonic() - t0,
            invocation_command=invocation,
            metadata=base_metadata,
        )

    stdout = proc.stdout if isinstance(proc.stdout, str) else ""
    # Only normalized deploy evidence is retained. The REST transport envelope
    # can contain response headers/cookies and must never be persisted here.
    raw_output = ""
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError) as e:
        return DispatchResult(
            surface="MetaAPI", status="ERROR",
            detail=(
                f"Exact deploy report returned invalid JSON (exit "
                f"{proc.returncode}): {e}"
            ),
            duration_seconds=time.monotonic() - t0,
            invocation_command=invocation,
            raw_output=raw_output,
            metadata=base_metadata,
        )

    if not isinstance(payload, dict) or not isinstance(payload.get("result"), dict):
        return DispatchResult(
            surface="MetaAPI", status="ERROR",
            detail=(
                f"Exact deploy report did not contain a structured result "
                f"(exit {proc.returncode})."
            ),
            duration_seconds=time.monotonic() - t0,
            invocation_command=invocation,
            raw_output=raw_output,
            metadata=base_metadata,
        )

    response = payload["result"]
    http_status = response.get("statusCode")
    envelope_status = payload.get("status")
    safe_envelope_status = envelope_status if type(envelope_status) is int else None
    body = response.get("body")
    base_metadata["report_http_status"] = (
        http_status if type(http_status) is int else None
    )
    if (
        proc.returncode != 0
        or type(payload.get("status")) is not int
        or payload.get("status") != 0
        or type(http_status) is not int
        or http_status != 200
        or not isinstance(body, dict)
        or not isinstance(body.get("deployResult"), dict)
    ):
        return DispatchResult(
            surface="MetaAPI", status="ERROR",
            detail=(
                "Exact deploy report transport was not conclusive: "
                f"exit={proc.returncode}, "
                f"envelope_status={safe_envelope_status!r}, "
                f"http_status={base_metadata['report_http_status']!r}; "
                "expected an HTTP 200 JSON deployResult."
            ),
            duration_seconds=time.monotonic() - t0,
            invocation_command=invocation,
            raw_output=raw_output,
            metadata=base_metadata,
        )

    # Preserve the previous private receipt schema, including the full exact
    # deployment result. Never retain transport headers or unrelated fields.
    payload = {"status": 0, "result": body["deployResult"]}
    raw_output = json.dumps(payload)
    result = payload["result"]
    reported_job_id = result.get("id")
    evidence_metadata = {
        **base_metadata,
        "reported_job_id": reported_job_id,
        "reported_status": result.get("status"),
        "check_only": result.get("checkOnly"),
    }

    reported_job_identity = _canonical_deploy_job_id(reported_job_id)
    if reported_job_identity is None or reported_job_identity != requested_job_identity:
        return DispatchResult(
            surface="MetaAPI", status="ERROR",
            detail=(
                f"Deploy report evidence mismatch: requested job "
                f"{deploy_job_id!r}, received {reported_job_id!r}."
            ),
            duration_seconds=time.monotonic() - t0,
            invocation_command=invocation,
            raw_output=raw_output,
            metadata=evidence_metadata,
        )

    target_mismatch = _meta_api_target_mismatch(payload, result, target_org)
    if target_mismatch:
        field_name, reported_target = target_mismatch
        return DispatchResult(
            surface="MetaAPI", status="ERROR",
            detail=(
                f"Deploy report target mismatch: requested {target_org!r}, "
                f"but {field_name} was {reported_target!r}."
            ),
            duration_seconds=time.monotonic() - t0,
            invocation_command=invocation,
            raw_output=raw_output,
            metadata={
                **evidence_metadata,
                "reported_target_field": field_name,
                "reported_target": reported_target,
            },
        )

    reported_status = result.get("status")
    normalized_status = (
        reported_status.replace(" ", "").casefold()
        if isinstance(reported_status, str) else ""
    )
    done = result.get("done")
    success = result.get("success")
    check_only = result.get("checkOnly")

    if normalized_status in _META_API_FAILURE_STATUSES or (done is True and success is False):
        return DispatchResult(
            surface="MetaAPI", status="FAIL",
            detail=(
                f"Exact {'validation' if check_only is True else 'deploy'} "
                f"{deploy_job_id} failed with status {reported_status!r}."
            ),
            duration_seconds=time.monotonic() - t0,
            invocation_command=invocation,
            raw_output=raw_output,
            metadata=evidence_metadata,
        )

    if done is False or normalized_status in _META_API_PENDING_STATUSES:
        return DispatchResult(
            surface="MetaAPI", status="MANUAL_REQUIRED",
            detail=(
                f"Exact deploy job {deploy_job_id} is not terminal "
                f"(status {reported_status!r}); rerun this QA check after it completes."
            ),
            duration_seconds=time.monotonic() - t0,
            invocation_command=invocation,
            raw_output=raw_output,
            metadata=evidence_metadata,
        )

    if (
        proc.returncode != 0
        or type(payload.get("status")) is not int
        or payload.get("status") != 0
        or reported_status != "Succeeded"
        or done is not True
        or success is not True
        or not isinstance(check_only, bool)
    ):
        return DispatchResult(
            surface="MetaAPI", status="ERROR",
            detail=(
                "Exact deploy report was not conclusive success evidence: "
                f"exit={proc.returncode}, envelope_status={payload.get('status')!r}, "
                f"deploy_status={reported_status!r}, done={done!r}, "
                f"success={success!r}, checkOnly={check_only!r}."
            ),
            duration_seconds=time.monotonic() - t0,
            invocation_command=invocation,
            raw_output=raw_output,
            metadata=evidence_metadata,
        )

    (
        reported_components,
        successful_components,
        unsuccessful_components,
        excluded_package_entries,
        component_evidence_error,
    ) = _extract_reported_components(result)
    if component_evidence_error:
        return DispatchResult(
            surface="MetaAPI", status="ERROR",
            detail=(
                "Exact deploy report lacked usable artifact evidence "
                f"(expected={len(expected_components)}, reported=unavailable"
                f"{manifest_hash_detail}): "
                f"{component_evidence_error}."
            ),
            duration_seconds=time.monotonic() - t0,
            invocation_command=invocation,
            raw_output=raw_output,
            metadata={
                **evidence_metadata,
                "reported_component_count": None,
                "matched_component_count": None,
                "missing_component_count": None,
                "excluded_package_xml_count": excluded_package_entries,
            },
        )

    # The helper's non-error contract guarantees these sets are present.
    assert reported_components is not None
    assert successful_components is not None
    assert unsuccessful_components is not None
    matched_components = expected_components & successful_components
    missing_components = expected_components - successful_components
    component_metadata = {
        **evidence_metadata,
        "reported_component_count": len(reported_components),
        "reported_components": sorted(f"{kind}:{name}" for kind, name in reported_components),
        "successful_components": sorted(f"{kind}:{name}" for kind, name in successful_components),
        "missing_components": sorted(f"{kind}:{name}" for kind, name in missing_components),
        "unsuccessful_components": sorted(f"{kind}:{name}" for kind, name in unsuccessful_components),
        "successful_reported_component_count": len(successful_components),
        "unsuccessful_reported_component_count": len(unsuccessful_components),
        "matched_component_count": len(matched_components),
        "missing_component_count": len(missing_components),
        "excluded_package_xml_count": excluded_package_entries,
    }
    if missing_components or unsuccessful_components:
        return DispatchResult(
            surface="MetaAPI", status="FAIL",
            detail=(
                f"Exact deploy job {deploy_job_id} artifact scope did not match "
                f"successful evidence: expected={len(expected_components)}, "
                f"reported={len(reported_components)}, "
                f"matched={len(matched_components)}, "
                f"missing={len(missing_components)}, "
                f"unsuccessful={len(unsuccessful_components)}"
                f"{manifest_hash_detail}."
            ),
            duration_seconds=time.monotonic() - t0,
            invocation_command=invocation,
            raw_output=raw_output,
            metadata=component_metadata,
        )

    component_errors = result.get("numberComponentErrors", 0)
    test_errors = result.get("numberTestErrors", 0)
    if (
        type(component_errors) is not int
        or component_errors < 0
        or type(test_errors) is not int
        or test_errors < 0
    ):
        return DispatchResult(
            surface="MetaAPI", status="ERROR",
            detail=(
                f"Exact deploy job {deploy_job_id} returned malformed error "
                f"counts: component_errors={component_errors!r}, "
                f"test_errors={test_errors!r}."
            ),
            duration_seconds=time.monotonic() - t0,
            invocation_command=invocation,
            raw_output=raw_output,
            metadata=component_metadata,
        )
    if (
        component_errors > 0
        or test_errors > 0
    ):
        return DispatchResult(
            surface="MetaAPI", status="FAIL",
            detail=(
                f"Exact deploy job {deploy_job_id} reported Succeeded but also "
                f"reported component_errors={component_errors!r}, "
                f"test_errors={test_errors!r}; refusing contradictory PASS evidence."
            ),
            duration_seconds=time.monotonic() - t0,
            invocation_command=invocation,
            raw_output=raw_output,
            metadata=component_metadata,
        )

    operation = "validation" if check_only else "deployment"
    return DispatchResult(
        surface="MetaAPI", status="PASS",
        detail=(
            f"Exact {operation} {deploy_job_id} reported Succeeded for target "
            f"context {target_org!r}; expected={len(expected_components)}, "
            f"reported={len(reported_components)}, "
            f"matched={len(matched_components)}"
            f"{manifest_hash_detail}."
        ),
        duration_seconds=time.monotonic() - t0,
        invocation_command=invocation,
        raw_output=raw_output,
        metadata={**component_metadata, "operation": operation},
    )


def dispatch_side_eff(target_org: str, change_description: str) -> DispatchResult:
    """Assess findings in the latest available debug log; never infer deployment coverage."""
    import time
    started = time.monotonic()
    cmd = [sys.executable, "-m", "jsc_loganalyzer.cli", "--target-org", target_org, "--json"]
    invocation = " ".join(cmd)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_MEDIUM)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DispatchResult("Side-Eff", "ERROR", f"Log analysis could not run: {type(exc).__name__}.", duration_seconds=time.monotonic()-started, invocation_command=invocation)
    if proc.returncode != 0:
        return DispatchResult("Side-Eff", "ERROR", f"Log analysis unavailable (exit {proc.returncode}); runtime health remains unknown.", duration_seconds=time.monotonic()-started, invocation_command=invocation)
    try:
        data = json.loads(proc.stdout)
        findings, summary = data["findings"], data["summary"]
        if not (isinstance(findings, list) and isinstance(summary, dict)): raise ValueError("shape")
        counts = {f"p{i-1}_count": sum(1 for item in findings if isinstance(item, dict) and type(item.get("severity")) is int and item["severity"] == i) for i in (1,2,3)}
        if not all(isinstance(item, dict) and type(item.get("severity")) is int and item["severity"] in (1,2,3) for item in findings): raise ValueError("finding")
        if not all(type(summary.get(key)) is int and summary[key] == value for key, value in counts.items()): raise ValueError("counts")
        if type(summary.get("total")) is not int or summary["total"] != len(findings): raise ValueError("total")
        if type(data.get("logs_analyzed")) is not int or data["logs_analyzed"] <= 0: raise ValueError("coverage")
        if data.get("error"): raise ValueError("error")
    except (ValueError, KeyError, TypeError, AssertionError):
        return DispatchResult("Side-Eff", "ERROR", "Analyzer evidence is missing, malformed or inconsistent; runtime health remains unknown.", duration_seconds=time.monotonic()-started, invocation_command=invocation)
    status = "FAIL" if counts["p0_count"] or counts["p1_count"] else "PASS"
    return DispatchResult(
        "Side-Eff", status,
        f"Analyzed {data['logs_analyzed']} available log(s): P0={counts['p0_count']}, P1={counts['p1_count']}, P2={counts['p2_count']}. This covers the available logs only; it does not prove the change executed or that unlogged effects are absent.",
        duration_seconds=time.monotonic()-started, invocation_command=invocation,
        metadata={"logs_analyzed": data["logs_analyzed"], "analysis_scope": data.get("analysis_scope", "available_logs"), "findings_summary": summary, "change_execution_proven": False},
    )

def dispatch_parity(target_org: str, change_description: str) -> DispatchResult:
    """Run an explicitly supplied client parity adapter and baseline org."""
    import time
    from pathlib import Path
    started = time.monotonic()
    adapter = os.environ.get("TORQUE_PARITY_SCRIPT")
    source_org = os.environ.get("TORQUE_BASELINE_ORG")
    if not adapter or not source_org:
        return DispatchResult("Parity", "MANUAL_REQUIRED", "Configure this client's config/parity.json with script and baseline_org for comparison (direct package callers may set TORQUE_PARITY_SCRIPT and TORQUE_BASELINE_ORG), or compare the stated fields manually.")
    path = Path(adapter).expanduser().resolve()
    if not path.is_file():
        return DispatchResult("Parity", "ERROR", "Configured parity adapter does not exist.")
    cmd = [sys.executable, str(path), "--source-org", source_org, "--fresh-org", target_org]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_LONG)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DispatchResult("Parity", "ERROR", str(exc))
    return DispatchResult("Parity", "PASS" if proc.returncode == 0 else "FAIL", "Configured parity adapter completed; scope is defined by that adapter.", duration_seconds=time.monotonic()-started, invocation_command=" ".join(cmd), raw_output=proc.stdout[-1500:])


def _discover_available_flows(browser_tests_dir, library_dir) -> list[str]:
    """Authoritative flow-name discovery for the browser-test library (A3).

    Uses jsc_browser_tests.suite.discover_flows() which recursively imports
    library/ + library/flows/** + library/flows/crud/** and derives names from
    each module's FLOW.spec.name. This catches the 20+ flows nested under
    library/flows/** that a non-recursive top-level `library_dir.glob("*.py")`
    silently misses.

    The browser_tests package dir is inserted on sys.path (R2-7) — NOT
    jsc_common's path; discover_flows lives in jsc_browser_tests.suite.

    Falls back to the OLD top-level glob WITH a logged caveat if the import
    fails (e.g. Playwright deps absent in the current env). The fallback name
    set is a strict subset of the authoritative one, so callers degrade
    gracefully rather than crashing.
    """
    import sys as _sys
    bt = str(browser_tests_dir)
    if bt not in _sys.path:
        _sys.path.insert(0, bt)
    try:
        from jsc_browser_tests.suite import discover_flows
        return sorted(f.spec.name for f in discover_flows())
    except Exception as e:  # ImportError (Playwright absent) or any discovery error
        import logging
        logging.getLogger(__name__).warning(
            "Funct-Pl/Multi-Prof: suite.discover_flows() unavailable (%s); "
            "falling back to non-recursive top-level glob — nested "
            "library/flows/** flows will NOT be discovered.", e,
        )
        return sorted(
            p.stem for p in library_dir.glob("*.py") if p.stem != "__init__"
        )


_IDENTITY_KEYS = ("org_id_18", "org_verified_by", "admin_before", "admin_username", "user_after_login_as",
                  "admin_restored", "restored", "status")


def _browser_identities(stdout: str) -> tuple[list[dict], list[str], str]:
    """From `jsc-browser-tests ... --json` output: each cell's identity report, one
    readable line per cell, and a redacted tail of the output. Only the identity keys
    are kept; the output is redacted again here, so no session URL is carried on."""
    from jsc_browser_tests.diagnostics import redact
    raw = redact(stdout or "")[-1000:]
    try:
        cells = json.loads(stdout or "")
    except ValueError:
        cells = None
    if not isinstance(cells, list):
        return [], ["identity: not reported (the browser route printed no results)"], raw
    identities, lines = [], []
    for item in cells:
        if not isinstance(item, dict):
            continue
        found = item.get("identity") if isinstance(item.get("identity"), dict) else {}
        entry = redact({"flow": item.get("flow"), "profile": item.get("profile"), "cell_status": item.get("status"),
                        **{key: found.get(key) for key in _IDENTITY_KEYS}})
        identities.append(entry)
        verified = " (verified by username)" if entry["org_verified_by"] == "username" else " (not verified)"
        lines.append(
            f"identity {entry['profile']}: org {entry['org_id_18']}{verified}, admin before "
            f"{entry['admin_before']} ({entry['admin_username']}), user after Login As "
            f"{entry['user_after_login_as']}, admin restored {entry['admin_restored']}, "
            f"restored {entry['restored']}, status {entry['status']}")
    return identities, lines or ["identity: not reported (no browser cells)"], raw


def dispatch_funct_pl(target_org: str, change_description: str) -> DispatchResult:
    """Funct-Pl surface: invoke browser_tests CLI if a library flow matches; else manual guidance.

    Phase 1B integration: the `change_description` is matched against available
    library flow names (substring). If exactly one matches, invoke
    `jsc-browser-tests browser <flow> --target-org <org>`. Otherwise emit
    manual guidance with the available flows listed.
    """
    import time
    from pathlib import Path
    t0 = time.monotonic()

    # Find packages/browser_tests/
    repo_root = workspace_root()
    browser_tests_dir = Path(__import__("jsc_browser_tests").__file__).resolve().parent.parent
    if not browser_tests_dir.exists():
        return DispatchResult(
            surface="Funct-Pl", status="MANUAL_REQUIRED",
            detail=(
                "packages/browser_tests/ not found. Manual: walk the affected feature "
                f"on {target_org} via UI; verify: {change_description[:200]}"
            ),
            invocation_command="(manual)",
        )

    library_dir = browser_tests_dir / "jsc_browser_tests" / "library"
    available_flows = _discover_available_flows(browser_tests_dir, library_dir)

    desc_lower = change_description.lower()
    matched = [f for f in available_flows if f.lower() in desc_lower]

    if len(matched) != 1:
        return DispatchResult(
            surface="Funct-Pl", status="MANUAL_REQUIRED",
            detail=(
                f"No unique library flow matched description "
                f"(matched={matched!r}). Available: {available_flows}.\n"
                f"To run: PYTHONPATH=packages/browser_tests python3 -m "
                f"jsc_browser_tests.cli browser <flow> --target-org {target_org}\n"
                f"Or use Chrome DevTools MCP turn-by-turn for ad-hoc."
            ),
            duration_seconds=time.monotonic() - t0,
            invocation_command="(manual)",
            metadata={"available_flows": available_flows, "matched": matched},
        )

    flow_name = matched[0]
    cmd = [
        sys.executable, "-m", "jsc_browser_tests.cli",
        "browser", flow_name, "--target-org", target_org, "--json",
    ]
    env_setup = f"PYTHONPATH={browser_tests_dir} "
    try:
        import os
        env = os.environ.copy()
        env["PYTHONPATH"] = str(browser_tests_dir)
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=TIMEOUT_LONG, env=env,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return DispatchResult(
            surface="Funct-Pl", status="ERROR",
            detail=f"jsc-browser-tests crashed: {e}",
            duration_seconds=time.monotonic() - t0,
            invocation_command=env_setup + " ".join(cmd),
        )
    status = "PASS" if proc.returncode == 0 else "FAIL"
    identities, lines, raw = _browser_identities(proc.stdout)
    return DispatchResult(
        surface="Funct-Pl", status=status,
        detail="\n".join([f"jsc-browser-tests browser {flow_name} exit {proc.returncode}", *lines]),
        duration_seconds=time.monotonic() - t0,
        invocation_command=env_setup + " ".join(cmd),
        raw_output=raw,
        metadata={"flow_name": flow_name, "identity": identities},
    )


def dispatch_funct_mcp(target_org: str, change_description: str) -> DispatchResult:
    """Funct-MCP surface: operator/Claude drives via mcp__plugin_chrome-devtools-mcp_*."""
    return DispatchResult(
        surface="Funct-MCP", status="MANUAL_REQUIRED",
        detail=(
            "Drive via Chrome DevTools MCP turn-by-turn. Per feedback_browser_testing_tool.md:\n"
            f"  1. mcp__plugin_chrome-devtools-mcp_chrome-devtools__list_pages\n"
            f"  2. mcp__plugin_chrome-devtools-mcp_chrome-devtools__navigate_page → {target_org} login URL\n"
            f"  3. Walk through: {change_description[:200]}\n"
            f"  4. Take screenshots at key steps; verify side effects via SOQL."
        ),
        invocation_command="(Claude turn-by-turn via DevTools MCP)",
    )


def dispatch_multi_prof(target_org: str, change_description: str) -> DispatchResult:
    """Multi-Prof surface: invoke browser_tests multiprofile if a library flow matches."""
    import time
    from pathlib import Path
    t0 = time.monotonic()

    repo_root = workspace_root()
    browser_tests_dir = Path(__import__("jsc_browser_tests").__file__).resolve().parent.parent
    library_dir = browser_tests_dir / "jsc_browser_tests" / "library"
    if not library_dir.exists():
        return DispatchResult(
            surface="Multi-Prof", status="MANUAL_REQUIRED",
            detail=(
                "packages/browser_tests/ not found. Manual checklist:\n"
                f"  1. Verify test-users.json seed exists: <selected-client>/config/test-users.json\n"
                f"  2. As Standard User: walk feature, verify FLS\n"
                f"  3. As Platform User: same walk\n"
                f"  4. Logout As back to admin"
            ),
            invocation_command="(manual)",
        )

    available_flows = _discover_available_flows(browser_tests_dir, library_dir)
    desc_lower = change_description.lower()
    matched = [f for f in available_flows if f.lower() in desc_lower]
    if len(matched) != 1:
        return DispatchResult(
            surface="Multi-Prof", status="MANUAL_REQUIRED",
            detail=(
                f"No unique library flow matched (matched={matched!r}). "
                f"Available: {available_flows}.\n"
                f"To run: PYTHONPATH=packages/browser_tests python3 -m "
                f"jsc_browser_tests.cli multiprofile <flow> --target-org {target_org}"
            ),
            duration_seconds=time.monotonic() - t0,
            invocation_command="(manual)",
            metadata={"available_flows": available_flows, "matched": matched},
        )

    flow_name = matched[0]
    cmd = [
        sys.executable, "-m", "jsc_browser_tests.cli",
        "multiprofile", flow_name, "--target-org", target_org, "--json",
    ]
    env_setup = f"PYTHONPATH={browser_tests_dir} "
    try:
        import os
        env = os.environ.copy()
        env["PYTHONPATH"] = str(browser_tests_dir)
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=TIMEOUT_LONG, env=env,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return DispatchResult(
            surface="Multi-Prof", status="ERROR",
            detail=f"jsc-browser-tests multiprofile crashed: {e}",
            duration_seconds=time.monotonic() - t0,
            invocation_command=env_setup + " ".join(cmd),
        )
    status = "PASS" if proc.returncode == 0 else "FAIL"
    identities, lines, raw = _browser_identities(proc.stdout)
    return DispatchResult(
        surface="Multi-Prof", status=status,
        detail="\n".join([f"jsc-browser-tests multiprofile {flow_name} exit {proc.returncode}", *lines]),
        duration_seconds=time.monotonic() - t0,
        invocation_command=env_setup + " ".join(cmd),
        raw_output=raw,
        metadata={"flow_name": flow_name, "identity": identities},
    )


def dispatch_adv_probe(target_org: str, change_description: str) -> DispatchResult:
    """Adv-Probe: jsc_probes adversarial Apex static synthesis.

    Invokes packages/adversarial_probes/jsc_probes apex CLI against any .cls
    paths mentioned in the change_description. .trigger paths are filtered out
    and surfaced as dropped (jsc_probes does not yet support trigger-aware
    synthesis per R1-Codex-007). If no .cls paths matched, emits MANUAL_REQUIRED
    instead of probing the whole repo (which would be expensive).
    """
    import re
    import time
    from pathlib import Path
    t0 = time.monotonic()
    repo_root = workspace_root()
    probes_dir = Path(__import__("jsc_probes").__file__).resolve().parent.parent
    if not probes_dir.exists():
        return DispatchResult(
            surface="Adv-Probe", status="MANUAL_REQUIRED",
            detail="packages/adversarial_probes/ not found.",
            invocation_command="(manual)",
        )

    # Extract any path-like tokens from the change description that look like apex files
    # R1 Codex-007: triggers handled wrong by jsc_probes — filter to .cls only for honesty
    raw_paths = re.findall(r"\S+\.(?:cls|trigger)\b", change_description)
    trigger_paths_dropped = [p for p in raw_paths if p.endswith(".trigger")]
    raw_paths = [p for p in raw_paths if p.endswith(".cls")]
    # Per audit codex-R3-P1-10: strip Markdown / quote punctuation from token edges
    _STRIP = "`'\"<>()[]{},;:"
    def _clean(p: str) -> str:
        return p.strip(_STRIP)
    def _resolve(p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else (repo_root / path)

    cleaned_paths = [_clean(p) for p in raw_paths]
    # R2 claude-P2-2: de-duplicate paths to prevent same-stem collision when
    # operator mentions the same file twice in the change description
    seen_paths: set[str] = set()
    existing_paths: list[str] = []
    for p in cleaned_paths:
        if not p:
            continue
        resolved = _resolve(p)
        if not resolved.exists():
            continue
        rstr = str(resolved.resolve())
        if rstr in seen_paths:
            continue
        seen_paths.add(rstr)
        existing_paths.append(rstr)
    dropped_paths = [p for p in cleaned_paths if not (p and _resolve(p).exists())]
    # Surface trigger-dropped paths so operator sees them
    for tp in trigger_paths_dropped:
        dropped_paths.append(f"{tp} (.trigger not yet supported — jsc_probes treats trigger as class; see R1-Codex-007)")

    if not existing_paths:
        # R2 gemini-P1-03: include --output flag so manual run doesn't write into codebase/
        detail = (
            "No existing .cls paths detected in change description.\n"
            f"  Raw matches: {raw_paths}\n"
            f"  Dropped (not found relative to repo root or as absolute paths): {dropped_paths}\n"
            f"To probe, include paths to existing apex files. Manual run:\n"
            f"  PYTHONPATH={probes_dir} python3 -m jsc_probes.cli "
            f"--target-class <path/to/file.cls> --output \"${{JSC_QA_ADV_PROBE_OUT:-/tmp/jsc-probes-out}}\""
        )
        return DispatchResult(
            surface="Adv-Probe", status="MANUAL_REQUIRED",
            detail=detail,
            duration_seconds=time.monotonic() - t0,
            invocation_command="(manual — provide existing apex paths)",
            metadata={"raw_paths": raw_paths, "dropped_paths": dropped_paths},
        )

    # R1 Codex-003: env var coercion — bad value → ERROR, not crash
    def _int_env(name: str, default: int, min_val: int = 1) -> tuple[int | None, str]:
        raw = os.environ.get(name)
        if raw is None or raw == "":
            return default, ""
        try:
            v = int(raw)
        except ValueError:
            return None, f"{name}={raw!r} is not a valid integer"
        if v < min_val:
            return None, f"{name}={v} is below minimum ({min_val}); set to at least {min_val} or unset to use default"
        return v, ""

    MAX_PROBE_FILES, err1 = _int_env("JSC_QA_ADV_PROBE_MAX_FILES", default=10, min_val=1)
    GLOBAL_PROBE_BUDGET_S, err2 = _int_env("JSC_QA_ADV_PROBE_BUDGET_S", default=300, min_val=10)
    if err1 or err2:
        return DispatchResult(
            surface="Adv-Probe", status="ERROR",
            detail=f"env var validation failed: {err1 or ''} {err2 or ''}".strip(),
            duration_seconds=time.monotonic() - t0,
            invocation_command="(env var invalid)",
        )

    total_identified = len(existing_paths)
    if len(existing_paths) > MAX_PROBE_FILES:
        truncated = existing_paths[MAX_PROBE_FILES:]
        for t in truncated:
            dropped_paths.append(f"{t} (over MAX_PROBE_FILES={MAX_PROBE_FILES})")
        existing_paths = existing_paths[:MAX_PROBE_FILES]
    probe_deadline = time.monotonic() + GLOBAL_PROBE_BUDGET_S
    env = os.environ.copy()
    env["PYTHONPATH"] = str(probes_dir)
    env_setup = f"PYTHONPATH={probes_dir} "

    # Use a fresh default directory; an explicit directory remains the caller's
    # choice. The generator preserves existing edits without legacy source shields.
    import tempfile as _tempfile
    import uuid as _uuid
    operator_out = os.environ.get("JSC_QA_ADV_PROBE_OUT")
    if operator_out:
        probe_output_dir = Path(operator_out).resolve()
        if probe_output_dir.exists() and not probe_output_dir.is_dir():
            return DispatchResult(
                surface="Adv-Probe", status="ERROR",
                detail=f"JSC_QA_ADV_PROBE_OUT={operator_out!r} exists but is not a directory",
                duration_seconds=time.monotonic() - t0,
                invocation_command="(output path is file, not dir)",
            )
    else:
        # Per-dispatch uuid subdir prevents cross-dispatch overwrite (R1-CONSENSUS-1)
        probe_output_dir = Path(_tempfile.gettempdir()) / f"jsc-probes-out-{_uuid.uuid4().hex[:12]}"
    try:
        probe_output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return DispatchResult(
            surface="Adv-Probe", status="ERROR",
            detail=f"failed to create output dir {probe_output_dir}: {e}",
            duration_seconds=time.monotonic() - t0,
            invocation_command="(mkdir failed)",
        )

    aggregated_stdout = []
    aggregated_stderr = []
    overall_exit = 0
    probed_count = 0
    generated_files: list[str] = []  # closes R1-CONSENSUS-1 reporting gap
    for ap in existing_paths:
        remaining_budget = probe_deadline - time.monotonic()
        if remaining_budget <= 5:
            dropped_paths.append(ap + " (budget exhausted)")
            continue
        per_file_timeout = min(TIMEOUT_MEDIUM, int(remaining_budget))
        # R1-CONSENSUS-1: include per-file source hash in output dir to prevent
        # same-stem collision (e.g. two `Foo.cls` from different paths)
        import hashlib as _hashlib
        per_file_hash = _hashlib.sha256(ap.encode()).hexdigest()[:8]
        per_file_out = probe_output_dir / f"hash-{per_file_hash}"
        try:
            per_file_out.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            # R2 codex-001: classify as ERROR DispatchResult rather than crash —
            # JSC_QA_ADV_PROBE_OUT may be writable for the parent but read-only
            # for new subdirs (mode 0o500), or filesystem-full, etc.
            return DispatchResult(
                surface="Adv-Probe", status="ERROR",
                detail=(
                    f"per-file output dir mkdir failed for {ap}: {e} "
                    f"(JSC_QA_ADV_PROBE_OUT={probe_output_dir} is not writable for new subdirs)"
                ),
                duration_seconds=time.monotonic() - t0,
                invocation_command="(per-file mkdir failed)",
            )
        cmd = [sys.executable, "-m", "jsc_probes.cli",
               "--target-class", ap,
               "--output", str(per_file_out)]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=per_file_timeout, env=env,
            )
            aggregated_stdout.append(f"--- {ap} (exit {proc.returncode}) ---\n{proc.stdout}")
            if proc.stderr:
                aggregated_stderr.append(f"--- {ap} ---\n{proc.stderr}")
            if proc.returncode != 0:
                overall_exit = proc.returncode
            probed_count += 1
            # Track generated files for reporting
            if proc.returncode == 0:
                for g in per_file_out.glob("*AdversarialTest.cls"):
                    generated_files.append(str(g))
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return DispatchResult(
                surface="Adv-Probe", status="ERROR",
                detail=f"jsc_probes.cli failed for {ap}: {e}",
                duration_seconds=time.monotonic() - t0,
                invocation_command=env_setup + " ".join(cmd),
            )

    # R1 Codex-001 + 002: status semantics — probed_count==0 with eligible_paths>0
    # means coverage gap (budget exhausted OR MAX_FILES truncated everything).
    if probed_count == 0 and len(existing_paths) > 0:
        return DispatchResult(
            surface="Adv-Probe", status="ERROR",
            detail=(
                f"Zero files probed despite {len(existing_paths)} eligible — "
                f"likely budget exhaustion (JSC_QA_ADV_PROBE_BUDGET_S={GLOBAL_PROBE_BUDGET_S}) "
                f"or filename truncation. Raise the budget or run jsc_probes.cli manually."
            ),
            duration_seconds=time.monotonic() - t0,
            invocation_command="(coverage gap — zero files probed)",
            metadata={
                "apex_paths": existing_paths,
                "dropped_paths": dropped_paths,
                "probed_count": probed_count,
                "eligible_count": len(existing_paths),
            },
        )

    # Artifact preparation is useful output, not verification. Even complete
    # generation has neither compiled nor executed the generated assertions.
    status = "MANUAL_REQUIRED" if overall_exit == 0 else "FAIL"
    if overall_exit == 0 and (
        len(generated_files) != probed_count
        or any(not Path(path + "-meta.xml").is_file() for path in generated_files)
    ):
        status = "ERROR"
    coverage_str = f"{probed_count} of {len(existing_paths)} eligible (of {total_identified} identified)"
    # R2 gemini-P0: success detail MUST surface "operator must deploy + run" caveat
    # so operator doesn't assume tests ran live in an org.
    # R2 gemini-P1-02: detail MUST include probe_output_dir so operator knows where
    # the generated AdversarialTest.cls files landed.
    detail = (
        f"jsc_probes.cli probed {coverage_str}; aggregate exit {overall_exit}. "
        f"GENERATED ONLY — {len(generated_files)} editable *AdversarialTest.cls "
        f"draft(s) at {probe_output_dir}. Review unsupported cases and replace each DRAFT "
        "assertion with a business expectation, then compile/run only the intended "
        "tests in the explicitly selected authorized org. Nothing was compiled or executed."
    )
    if total_identified > MAX_PROBE_FILES:
        detail += f" — WARNING: only first {MAX_PROBE_FILES} of {total_identified} files probed (raise via JSC_QA_ADV_PROBE_MAX_FILES)"
    if dropped_paths:
        detail += f" — dropped: {dropped_paths}"
    raw_output = ("\n".join(aggregated_stdout)
                  + ("\n\n--STDERR--\n" + "\n".join(aggregated_stderr) if aggregated_stderr else ""))[-2000:]
    return DispatchResult(
        surface="Adv-Probe", status=status,
        detail=detail,
        duration_seconds=time.monotonic() - t0,
        invocation_command=env_setup + f"python3 -m jsc_probes.cli --target-class <each of {len(existing_paths)} files>",
        raw_output=raw_output,
        metadata={
            "apex_paths": existing_paths,
            "dropped_paths": dropped_paths,
            "probed_count": probed_count,
            "eligible_count": len(existing_paths),
            "probe_output_dir": str(probe_output_dir),
            "generated_files": generated_files,
            "generated": bool(generated_files),
            "compiled": False,
            "executed": False,
            "identified_count": total_identified,
            "generated_count": len(generated_files),
            "dropped_count": len(dropped_paths),
        },
    )


def dispatch_hook_gate(target_org: str, change_description: str) -> DispatchResult:
    """Legacy surface retained without claiming uninstalled hooks executed."""
    return DispatchResult("Hook-Gate", "DEFERRED", "Torque does not install global command hooks. Review the selected workflow's actual checks; this surface is not evidence of validation.")


def dispatch_taa(target_org: str, change_description: str) -> DispatchResult:
    """TAA: emit guidance — operator must explicitly invoke."""
    return DispatchResult(
        surface="TAA", status="MANUAL_REQUIRED",
        detail=(
            "TAA is EXPENSIVE (3-model panel + synthesis). Operator opt-in only.\n"
            "Per multi-model-audit.md: reserve for behavioral-rule changes + ship gates.\n"
            "To invoke: scripts/run-taa-audit.sh OR manual dispatch per "
            ".claude/rules/multi-model-audit.md."
        ),
        invocation_command="(operator-explicit)",
    )


def dispatch_hostile_qa(target_org: str, change_description: str) -> DispatchResult:
    """Hostile-QA: emit guidance — operator must explicitly invoke (per Gemini-R1-P0-1)."""
    return DispatchResult(
        surface="Hostile-QA", status="MANUAL_REQUIRED",
        detail=(
            "Hostile-QA is EXPENSIVE (Opus subagent dispatch). Operator opt-in only.\n"
            "Per Gemini-R1-P0-1 + multi-model-audit.md: do NOT auto-dispatch on QA failure.\n"
            "To invoke: dispatch the hostile-qa-auditor subagent via Agent tool with a "
            "specific brief about the change."
        ),
        invocation_command="(operator-explicit subagent dispatch)",
    )


_TARGET_ORG_RE = __import__("re").compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# B1: the default prod-detect heuristic + its precedence now live in
# jsc_common.org_classify (policy="dispatcher_alias_first"). The former
# _DEFAULT_PROD_ALIAS_RE (\b(prod|production|live)\b, word-boundary to avoid
# false-positives on sf-nonprod / sf-preprod / sf-prodcopy) moved there.
# Operator overrides remain JSC_PROD_ORG_PATTERN / JSC_PROD_ALIASES per
# hooks-discipline.md; NO hardcoded client aliases in framework code
# (privacy-and-logging.md). Closes R2-CONSENSUS-1.
CLAUDE_ONLY_CAVEAT = (
    "\n\n[ORCHESTRATION CAVEAT — claude_only environment]\n"
    "Per .claude/rules/model-orchestration.md, this surface is gemini-optional. "
    "Operator should re-audit at higher capability environment when CLI becomes available."
)


def _validate_target_org(target_org: str) -> str | None:
    """Return error string if target_org is unsafe; None if OK.

    Closes R1 CONSENSUS-4: target_org was interpolated into Path with no
    sanitization — absolute paths and `..` could escape qa_root.
    """
    if not target_org or not isinstance(target_org, str):
        return "target_org must be a non-empty string"
    if not _TARGET_ORG_RE.match(target_org):
        return (
            f"target_org {target_org!r} is not a valid Salesforce alias "
            f"(must match ^[A-Za-z0-9._-]+$)"
        )
    return None


def _live_org_is_production(target_org: str) -> bool | None:
    """Authoritative live classification via org_detect.resolve_org
    (`sf org display`). True=production, False=sandbox, None=unresolvable or
    skipped (JSC_VISION_SKIP_LIVE_ORG_DETECT=1).

    Retained as a module-level seam (B1): the live probe body now lives in
    jsc_common (LAZY import — keeps the STRING-ONLY hooks off jsc_revert), but
    `_is_production_alias` injects THIS function as jsc_common's `live_resolver`
    so it stays cheap to monkeypatch in unit tests.
    """
    if os.environ.get("JSC_VISION_SKIP_LIVE_ORG_DETECT") == "1":
        return None
    try:
        import sys as _sys
        _revert = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "revert",
        )
        if _revert not in _sys.path:
            _sys.path.insert(0, _revert)
        from jsc_revert import org_detect as _od  # type: ignore
        info = _od.resolve_org(target_org)
    except Exception:
        return None
    if info is None:
        return None
    if info.detected_org_type == "production":
        return True
    # 'developer' is non-production (added 2026-07-28 with the classifier
    # unification). Omitting it here would fail closed to production severity
    # for every Developer Edition org, which is the bug being fixed.
    if info.detected_org_type in ("sandbox", "developer"):
        return False
    return None


def _is_production_alias(target_org: str) -> bool:
    """Resolve the Vision PII target from authoritative org identity.

    Aliases and legacy trust environment variables cannot prove an org is
    nonproduction. Unknown or incomplete live identity is production-like.
    The policy name is retained for compatibility with the shared classifier.
    """
    return is_production_target(
        target_org,
        policy="dispatcher_alias_first",
        live_resolver=_live_org_is_production,
    )


def _vision_manifest_inventory(target_org: str):
    """Read the newest target-scoped manifest and its explicit screenshots.

    Only the selected client's state is considered. Both the current encoded
    target directory and the original alias directory are supported, but both
    require a manifest that explicitly names the exact requested target. A
    newer empty, invalid, or incomplete run never falls back to older evidence.
    """
    import hashlib
    from pathlib import Path
    from jsc_browser_tests.diagnostics import artifact_component

    qa_base = state_dir("qa-tests").resolve()
    scope = workspace_root().resolve()
    if not qa_base.is_relative_to(scope):
        raise ValueError("Browser evidence path escapes the selected client")
    manifests = []
    for label in dict.fromkeys((artifact_component(target_org), target_org)):
        target_root = qa_base / label
        if target_root.is_symlink():
            raise ValueError("Browser target evidence directory must not be a symlink")
        if not target_root.exists():
            continue
        if not target_root.is_dir() or not target_root.resolve().is_relative_to(qa_base):
            raise ValueError("Browser target evidence directory escapes the selected client")
        for directory, dirs, files in os.walk(target_root, followlinks=False):
            current = Path(directory)
            if any((current / name).is_symlink() for name in dirs):
                raise ValueError("Browser run evidence directory must not be a symlink")
            for filename in ("manifest.json", "run-manifest.json"):
                if filename not in files:
                    continue
                candidate = current / filename
                if candidate.is_symlink() or not candidate.resolve().is_relative_to(target_root.resolve()):
                    raise ValueError("Browser manifest escapes its target directory")
                manifests.append(candidate)
    if not manifests:
        return None, {}, []
    manifest_path = max(manifests, key=lambda path: (path.stat().st_mtime_ns, str(path)))
    raw = manifest_path.read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("target_org") != target_org:
        raise ValueError("Latest browser manifest does not identify the exact requested target; rerun browser capture")
    # Current suite schema uses cells; original browser CLI used results.
    cells = payload.get("cells", payload.get("results"))
    if not isinstance(cells, list):
        raise ValueError("Latest browser manifest has no structured cells/results list")
    run_root = manifest_path.parent.resolve()
    screenshots = []
    seen = {}
    for cell in cells:
        if not isinstance(cell, dict):
            raise ValueError("Latest browser manifest contains an invalid cell")
        if cell.get("target_org", target_org) != target_org:
            raise ValueError("Browser manifest mixes target orgs")
        flow = cell.get("flow", cell.get("flow_name"))
        profile = cell.get("profile")
        steps = cell.get("steps", [])
        if not isinstance(steps, list):
            raise ValueError("Browser manifest contains invalid steps")
        for step in steps:
            if not isinstance(step, dict):
                raise ValueError("Browser manifest contains an invalid step")
            reference = step.get("screenshot_path", step.get("screenshot"))
            if reference is None or reference == "":
                continue
            if not isinstance(reference, str):
                raise ValueError("Browser screenshot reference must be a path")
            if not isinstance(flow, str) or not flow.strip() or not isinstance(profile, str) or not profile.strip():
                raise ValueError("Browser screenshot lacks manifest flow/profile context")
            step_name = step.get("step_name", step.get("step", step.get("name", "unknown")))
            if not isinstance(step_name, str) or not step_name.strip():
                raise ValueError("Browser screenshot step name is invalid")
            source = Path(reference)
            if not source.is_absolute():
                source = run_root / source
            resolved = source.resolve()
            if (not resolved.is_relative_to(run_root)
                    or not resolved.is_relative_to(qa_base)
                    or not resolved.is_file() or resolved.suffix.lower() != ".png"):
                raise ValueError("Browser screenshot is missing or outside its selected run")
            context = (flow, profile, step_name)
            if resolved in seen:
                if seen[resolved] != context:
                    raise ValueError("Browser screenshot has conflicting manifest flow/profile/step context")
                continue
            seen[resolved] = context
            screenshots.append({"path": str(resolved), "flow": flow,
                                "profile": profile, "step_name": step_name})
    metadata = {
        "target_org": target_org,
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "run_dir": str(run_root),
        "runid": payload.get("runid"),
        "screenshot_count": len(screenshots),
        "screenshots": screenshots,
        "browser_cells": [{"flow": cell.get("flow", cell.get("flow_name")),
                           "profile": cell.get("profile"),
                           "status": cell.get("status", cell.get("overall_status"))}
                          for cell in cells],
    }
    return manifest_path, metadata, screenshots


def dispatch_vision(target_org: str, change_description: str) -> DispatchResult:
    """Review explicit screenshots from the newest selected-client manifest.

    The optional analyzer receives each cell's recorded flow, profile, and step.
    Its result describes screenshot review, not browser execution or user-role
    acceptance; those outcomes remain visible in the browser manifest.
    """
    import time
    from pathlib import Path
    t0 = time.monotonic()

    err = _validate_target_org(target_org)
    if err:
        return DispatchResult("Vision", "ERROR", err,
                              duration_seconds=time.monotonic() - t0,
                              invocation_command="(invalid target_org)")

    # Retain the existing optional provider-disclosure setting for this route.
    if _is_production_alias(target_org) and os.environ.get("JSC_VISION_PROD_OK") != "1":
        return DispatchResult(
            surface="Vision", status="MANUAL_REQUIRED",
            detail=(f"BLOCKED: target_org {target_org!r} appears to be production. "
                    "Vision sends screenshots to the configured Gemini provider. "
                    "Review this run's screenshots and the authorized provider context "
                    "before opting in with JSC_VISION_PROD_OK=1; manual review is available."),
            duration_seconds=time.monotonic() - t0,
            invocation_command="(production-blocked)",
        )
    try:
        from jsc_browser_tests import vision as _vision
    except ImportError:
        return DispatchResult("Vision", "MANUAL_REQUIRED",
                              "Browser vision package is unavailable; review screenshots manually.",
                              duration_seconds=time.monotonic() - t0)

    try:
        manifest_path, metadata, screenshots = _vision_manifest_inventory(target_org)
    except (OSError, RuntimeError, ValueError) as exc:
        return DispatchResult(
            "Vision", "ERROR", f"Browser evidence could not be verified: {exc}",
            duration_seconds=time.monotonic() - t0,
            invocation_command="(invalid browser evidence)",
        )
    if manifest_path is None:
        return DispatchResult(
            "Vision", "MANUAL_REQUIRED",
            f"No browser manifest found for {target_org!r} in the selected client's state. "
            "Run /qa-browser to capture screenshots, then retry Vision or review them manually.",
            duration_seconds=time.monotonic() - t0,
            invocation_command="(awaiting browser run)",
        )
    if not screenshots:
        return DispatchResult(
            "Vision", "MANUAL_REQUIRED",
            "The latest browser manifest contains no screenshots. Older runs were not substituted.",
            duration_seconds=time.monotonic() - t0,
            invocation_command="(no screenshots)", metadata=metadata,
        )
    if not _vision.gemini_available():
        return DispatchResult(
            "Vision", "MANUAL_REQUIRED",
            f"Optional gemini CLI is unavailable. {len(screenshots)} screenshot(s) "
            f"listed by {manifest_path} are available for manual review."
            + CLAUDE_ONLY_CAVEAT,
            duration_seconds=time.monotonic() - t0,
            invocation_command="(gemini absent — manual review)", metadata=metadata,
        )
    results = [
        _vision.analyze_screenshot(shot["path"], flow_name=shot["flow"],
                                   profile=shot["profile"], step_name=shot["step_name"])
        for shot in screenshots
    ]
    total = len(results)
    err_statuses = {"ERROR", "MODELS_EXHAUSTED", "STAGING_FAIL", "PARSE_FAIL", "AUTH_FAILED"}
    err_count = sum(r.status in err_statuses for r in results)
    incomplete_count = sum(r.status not in ("OK", "WARN") for r in results)
    warn_count = sum(r.status == "WARN" for r in results)
    p0p1 = sum(f.severity in ("P0", "P1") for r in results for f in r.findings)
    if err_count == total:
        status = "ERROR"
    elif p0p1:
        status = "FAIL"
    elif incomplete_count:
        status = "MANUAL_REQUIRED"
    else:
        status = "PASS"
    detail_lines = [
        f"Reviewed {total} manifest-listed screenshot(s) from {manifest_path.parent.name}.",
        f"Errors: {err_count}; incomplete analyses: {incomplete_count}; warnings: {warn_count}; P0/P1 findings: {p0p1}.",
    ]
    for result in results[:5]:
        detail_lines.append(f"- {Path(result.screenshot_path).name}: {result.status} "
                            f"({len(result.findings)} findings, model={result.model or 'n/a'})")
    detail_lines.append("Screenshot review does not establish browser execution or user-role acceptance; consult the recorded browser outcomes.")
    return DispatchResult(
        "Vision", status, "\n".join(detail_lines),
        duration_seconds=time.monotonic() - t0,
        invocation_command="(manifest-listed screenshot analysis via jsc_browser_tests.vision.analyze_screenshot)",
        metadata={**metadata, "p0p1_count": p0p1, "results": [r.to_dict() for r in results]},
    )


def dispatch_a11y(target_org: str, change_description: str) -> DispatchResult:
    return DispatchResult(
        surface="a11y", status="DEFERRED",
        detail="Phase 2-N. Not yet implemented (axe-core + Playwright integration planned).",
        invocation_command="(deferred)",
    )


def dispatch_visual_reg(target_org: str, change_description: str) -> DispatchResult:
    return DispatchResult(
        surface="Visual-Reg", status="DEFERRED",
        detail="Phase 2-N. Not yet implemented (screenshot baseline + diff planned).",
        invocation_command="(deferred)",
    )


def dispatch_ai_prompt(target_org: str, change_description: str) -> DispatchResult:
    """Replay explicitly configured client prompt fixtures through the provider harness."""
    error = _validate_target_org(target_org)
    if error:
        return DispatchResult("AI-Prompt", "ERROR", error)
    if not os.environ.get("TORQUE_AI_FIXTURES"):
        return DispatchResult("AI-Prompt", "MANUAL_REQUIRED", "Configure this client's config/ai-fixtures directory to run prompt regression (direct package callers may set TORQUE_AI_FIXTURES). Bundled synthetic fixtures demonstrate the contract only.")
    return _dispatch_ai_prompt_active(target_org, change_description)


def _dispatch_ai_prompt_active(target_org: str, change_description: str) -> DispatchResult:
    """Run provider fixtures configured for this client; report contract evidence only."""
    import sys
    import time
    from pathlib import Path
    t0 = time.monotonic()

    err = _validate_target_org(target_org)
    if err:
        return DispatchResult(
            surface="AI-Prompt", status="ERROR",
            detail=err,
            duration_seconds=time.monotonic() - t0,
            invocation_command="(invalid target_org)",
        )

    repo_root = workspace_root()
    pkg_dir = Path(__import__("jsc_ai_prompt_regression").__file__).resolve().parent.parent
    fixtures_root = Path(os.environ["TORQUE_AI_FIXTURES"]).expanduser().resolve()

    if not pkg_dir.exists():
        return DispatchResult(
            surface="AI-Prompt", status="MANUAL_REQUIRED",
            detail="packages/ai_prompt_regression/ not found.",
            duration_seconds=time.monotonic() - t0,
            invocation_command="(manual)",
        )
    if not fixtures_root.exists():
        return DispatchResult(
            surface="AI-Prompt", status="MANUAL_REQUIRED",
            detail=f"No fixtures dir at {fixtures_root}. "
                   f"Add a fixture to enable AI-Prompt dispatch.",
            duration_seconds=time.monotonic() - t0,
            invocation_command="(no fixtures)",
        )

    sys.path.insert(0, str(pkg_dir))
    try:
        from jsc_ai_prompt_regression import harness as _harness
    except ImportError as e:
        return DispatchResult(
            surface="AI-Prompt", status="ERROR",
            detail=f"import failed: {e}",
            duration_seconds=time.monotonic() - t0,
            invocation_command="(import failed)",
        )
    finally:
        if str(pkg_dir) in sys.path:
            sys.path.remove(str(pkg_dir))

    if not _harness.gemini_available():
        fixture_dirs = sorted(p.name for p in fixtures_root.iterdir() if p.is_dir())
        return DispatchResult(
            surface="AI-Prompt", status="MANUAL_REQUIRED",
            detail=(
                f"gemini CLI not installed (AI-Prompt optional per model-orchestration.md). "
                f"{len(fixture_dirs)} fixture(s) available for manual review: "
                + ", ".join(fixture_dirs[:10])
                + ("…" if len(fixture_dirs) > 10 else "")
                + CLAUDE_ONLY_CAVEAT
            ),
            duration_seconds=time.monotonic() - t0,
            invocation_command="(gemini absent — manual review)",
            metadata={"fixtures_root": str(fixtures_root), "fixture_count": len(fixture_dirs)},
        )

    results = _harness.replay_directory(fixtures_root)
    if not results:
        return DispatchResult(
            surface="AI-Prompt", status="MANUAL_REQUIRED",
            detail=f"No fixtures found under {fixtures_root}.",
            duration_seconds=time.monotonic() - t0,
            invocation_command="(no fixtures)",
        )

    total = len(results)
    passed = sum(1 for r in results if r.status == "PASS")
    failed_status = sum(1 for r in results if r.status not in ("PASS",))

    if passed == total:
        status = "PASS"
    elif passed == 0:
        status = "FAIL"
    else:
        status = "FAIL"  # any failure is a regression

    detail_lines = [
        f"Replayed {total} prompt fixture(s); {passed} PASS, {failed_status} FAIL/other",
    ]
    for r in results[:5]:
        finding_count = len(r.validation.findings) if r.validation else 0
        detail_lines.append(
            f"  - {r.fixture_name}: {r.status} "
            f"({finding_count} validation finding(s), model={r.model or 'n/a'})"
        )
    if len(results) > 5:
        detail_lines.append(f"  …and {len(results) - 5} more")

    # R1 gemini-R1-P2-04: invocation_command must be runnable shell form
    invocation = (
        "PYTHONPATH=packages/ai_prompt_regression python3 -m "
        f"jsc_ai_prompt_regression.cli replay-all {fixtures_root}"
    )
    return DispatchResult(
        surface="AI-Prompt", status=status,
        detail="\n".join(detail_lines) + "\nFixture contract checks do not prove deployed Salesforce prompt execution.",
        duration_seconds=time.monotonic() - t0,
        invocation_command=invocation,
        metadata={
            "fixtures_root": str(fixtures_root),
            "fixture_count": total,
            "passed": passed,
            "failed": failed_status,
            "results": [r.to_dict() for r in results],
        },
    )


# Surface dispatcher registry
DISPATCHERS = {
    "MetaAPI": dispatch_meta_api,
    "Side-Eff": dispatch_side_eff,
    "Parity": dispatch_parity,
    "Funct-Pl": dispatch_funct_pl,
    "Funct-MCP": dispatch_funct_mcp,
    "Multi-Prof": dispatch_multi_prof,
    "Adv-Probe": dispatch_adv_probe,
    "Hook-Gate": dispatch_hook_gate,
    "TAA": dispatch_taa,
    "Hostile-QA": dispatch_hostile_qa,
    "Vision": dispatch_vision,
    "AI-Prompt": dispatch_ai_prompt,
    "a11y": dispatch_a11y,
    "Visual-Reg": dispatch_visual_reg,
}


def dispatch_surface(
    surface_name: str,
    target_org: str,
    change_description: str,
    *,
    deploy_job_id: str | None = None,
    deploy_components: list[str] | tuple[str, ...] | None = None,
    deploy_manifest: str | None = None,
) -> DispatchResult:
    """Dispatch a single surface by name. Returns DispatchResult.

    deploy_job_id, deploy_components, and deploy_manifest are consumed only by
    MetaAPI. Keeping them explicit at this boundary lets the CLI bind evidence
    to the deployment under review without changing every other surface's call
    contract.
    """
    fn = DISPATCHERS.get(surface_name)
    if fn is None:
        return DispatchResult(
            surface=surface_name, status="ERROR",
            detail=f"unknown surface: {surface_name!r}",
            invocation_command="(unknown)",
        )
    if surface_name == "MetaAPI":
        return fn(
            target_org,
            change_description,
            deploy_job_id=deploy_job_id,
            deploy_components=deploy_components,
            deploy_manifest=deploy_manifest,
        )
    return fn(target_org, change_description)
