"""Hermetic tests for exact-job MetaAPI evidence handling.

Every sf CLI interaction is mocked. These tests must never contact an org.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jsc_qa import cli, dispatcher, router


JOB_ID = "0AfVs000001ojZFKAY"
OTHER_JOB_ID = "0Af0x000017yLUFCA2"
TARGET_ORG = "sf-test"
EXPECTED_COMPONENTS = (
    "Flow:Client_Intake",
    "CustomField:Matter__c.Referral_Status__c",
)


def _component_success(component_spec, *, success=True):
    component_type, full_name = component_spec.split(":", 1)
    return {
        "componentType": component_type,
        "fullName": full_name,
        "fileName": f"force-app/{full_name}",
        "success": success,
    }


def _manifest_xml(component_specs=EXPECTED_COMPONENTS):
    by_type = {}
    for component_spec in component_specs:
        component_type, full_name = component_spec.split(":", 1)
        by_type.setdefault(component_type, []).append(full_name)
    type_blocks = []
    for component_type, full_names in by_type.items():
        members = "".join(
            f"<members>{full_name}</members>" for full_name in full_names
        )
        type_blocks.append(
            f"<types>{members}<name>{component_type}</name></types>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Package xmlns="http://soap.sforce.com/2006/04/metadata">'
        + "".join(type_blocks)
        + "<version>65.0</version></Package>"
    )


def _payload(**result_overrides):
    result = {
        "id": JOB_ID,
        "status": "Succeeded",
        "success": True,
        "done": True,
        "checkOnly": False,
        "numberComponentErrors": 0,
        "numberTestErrors": 0,
        "details": {
            "componentSuccesses": [
                {
                    "componentType": "",
                    "fullName": "package.xml",
                    "fileName": "package.xml",
                    "success": True,
                },
                *[_component_success(spec) for spec in EXPECTED_COMPONENTS],
            ],
        },
    }
    result.update(result_overrides)
    return {"status": 0, "result": result}


def _completed(payload, *, returncode=0, stderr=""):
    if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
        payload = {
            "status": payload.get("status"),
            "result": {
                "statusCode": 200,
                "headers": {},
                "body": {"deployResult": payload["result"]},
            },
        }
    stdout = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class MetaApiDispatcherTests(unittest.TestCase):
    def test_missing_job_id_is_manual_required_and_never_invokes_sf(self):
        with mock.patch.object(dispatcher.subprocess, "run") as run:
            result = dispatcher.dispatch_meta_api(
                TARGET_ORG,
                "A3 Flow change",
                deploy_components=EXPECTED_COMPONENTS,
            )

        self.assertEqual("MANUAL_REQUIRED", result.status)
        self.assertIn("--deploy-job-id", result.detail)
        self.assertNotIn("missing: at least one --deploy-component", result.detail)
        self.assertEqual(2, result.metadata["expected_component_count"])
        run.assert_not_called()

    def test_missing_expected_components_is_manual_required_before_sf(self):
        for components in (None, [], ()):
            with self.subTest(components=components):
                with mock.patch.object(dispatcher.subprocess, "run") as run:
                    result = dispatcher.dispatch_meta_api(
                        TARGET_ORG,
                        "A3 Flow change",
                        deploy_job_id=JOB_ID,
                        deploy_components=components,
                    )
                self.assertEqual("MANUAL_REQUIRED", result.status)
                self.assertIn("--deploy-component", result.detail)
                self.assertEqual(0, result.metadata["expected_component_count"])
                self.assertIsNone(result.metadata["reported_component_count"])
                run.assert_not_called()

    def test_invalid_expected_component_specs_are_error_before_sf(self):
        invalid_values = (
            "Flow:Client_Intake",
            ["Flow"],
            [":Client_Intake"],
            ["Flow:"],
            ["Flow:\nClient_Intake"],
            [123],
        )
        for components in invalid_values:
            with self.subTest(components=components):
                with mock.patch.object(dispatcher.subprocess, "run") as run:
                    result = dispatcher.dispatch_meta_api(
                        TARGET_ORG,
                        "A3 Flow change",
                        deploy_job_id=JOB_ID,
                        deploy_components=components,
                    )
                self.assertEqual("ERROR", result.status)
                self.assertIn("Invalid expected deploy component", result.detail)
                run.assert_not_called()

    def test_missing_or_malformed_manifest_is_error_before_sf(self):
        namespace = "http://soap.sforce.com/2006/04/metadata"
        invalid_manifests = (
            "",
            "<Package>",
            "<NotPackage><version>65.0</version></NotPackage>",
            f'<Package xmlns="{namespace}"></Package>',
            f'<Package xmlns="{namespace}"><version>nope</version></Package>',
            (
                f'<Package xmlns="{namespace}">'
                "<types><members>*</members><name>Flow</name></types>"
                "<version>65.0</version></Package>"
            ),
            (
                f'<Package xmlns="{namespace}">'
                "<types><members>Client_Intake</members></types>"
                "<version>65.0</version></Package>"
            ),
            (
                f'<Package xmlns="{namespace}">'
                "<types><name>Flow</name></types>"
                "<version>65.0</version></Package>"
            ),
            (
                '<!DOCTYPE Package [<!ENTITY x "Flow">]>'
                f'<Package xmlns="{namespace}"><version>65.0</version></Package>'
            ),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            for index, manifest_text in enumerate(invalid_manifests):
                manifest_path = Path(temp_dir) / f"invalid-{index}.xml"
                manifest_path.write_text(manifest_text, encoding="utf-8")
                with self.subTest(index=index):
                    with mock.patch.object(dispatcher.subprocess, "run") as run:
                        result = dispatcher.dispatch_meta_api(
                            TARGET_ORG,
                            "A3 Flow change",
                            deploy_job_id=JOB_ID,
                            deploy_manifest=str(manifest_path),
                        )
                    self.assertEqual("ERROR", result.status)
                    self.assertIn("Invalid deploy manifest", result.detail)
                    self.assertNotIn(str(manifest_path), result.detail)
                    run.assert_not_called()

            missing_path = Path(temp_dir) / "missing.xml"
            with mock.patch.object(dispatcher.subprocess, "run") as run:
                result = dispatcher.dispatch_meta_api(
                    TARGET_ORG,
                    "A3 Flow change",
                    deploy_job_id=JOB_ID,
                    deploy_manifest=str(missing_path),
                )
            self.assertEqual("ERROR", result.status)
            self.assertIn("Invalid deploy manifest", result.detail)
            self.assertNotIn(str(missing_path), result.detail)
            run.assert_not_called()

    def test_invalid_job_id_is_error_and_never_invokes_sf(self):
        for invalid in (
            "latest",
            "0Af-too-short",
            " 0AfVs000001ojZFKAY",
            "0AGVs000001ojZFKAY",
            0,
        ):
            with self.subTest(invalid=invalid):
                with mock.patch.object(dispatcher.subprocess, "run") as run:
                    result = dispatcher.dispatch_meta_api(
                        TARGET_ORG,
                        "A3 Flow change",
                        deploy_job_id=invalid,
                        deploy_components=EXPECTED_COMPONENTS,
                    )
                self.assertEqual("ERROR", result.status)
                self.assertIn("Invalid deploy job ID", result.detail)
                run.assert_not_called()

    def test_invalid_target_is_error_and_never_invokes_sf(self):
        for invalid in ("", "sf test", " sf-test", None):
            with self.subTest(invalid=invalid):
                with mock.patch.object(dispatcher.subprocess, "run") as run:
                    result = dispatcher.dispatch_meta_api(
                        invalid,
                        "A3 Flow change",
                        deploy_job_id=JOB_ID,
                        deploy_components=EXPECTED_COMPONENTS,
                    )
                self.assertEqual("ERROR", result.status)
                self.assertIn("Invalid target org", result.detail)
                run.assert_not_called()

    def test_exact_job_and_target_are_passed_to_sf_without_latest_fallback(self):
        with mock.patch.object(
            dispatcher.subprocess,
            "run",
            return_value=_completed(_payload()),
        ) as run:
            result = dispatcher.dispatch_meta_api(
                TARGET_ORG,
                "A3 Flow change",
                deploy_job_id=JOB_ID,
                deploy_components=EXPECTED_COMPONENTS,
            )

        self.assertEqual("PASS", result.status)
        command = run.call_args.args[0]
        self.assertEqual(
            [
                "sf", "api", "request", "rest",
                f"/services/data/v65.0/metadata/deployRequest/{JOB_ID}?includeDetails=true",
                "--method", "GET",
                "--target-org", TARGET_ORG,
                "--json",
            ],
            command,
        )
        self.assertNotIn("--use-most-recent", command)
        self.assertEqual(dispatcher.TIMEOUT_MEDIUM, run.call_args.kwargs["timeout"])

    def test_succeeded_deploy_is_pass_with_exact_evidence_metadata(self):
        with mock.patch.object(
            dispatcher.subprocess,
            "run",
            return_value=_completed(_payload()),
        ):
            result = dispatcher.dispatch_meta_api(
                TARGET_ORG,
                "metadata change",
                deploy_job_id=JOB_ID,
                deploy_components=EXPECTED_COMPONENTS,
            )

        self.assertEqual("PASS", result.status)
        self.assertEqual("deployment", result.metadata["operation"])
        self.assertEqual(JOB_ID, result.metadata["requested_job_id"])
        self.assertEqual(JOB_ID, result.metadata["reported_job_id"])
        self.assertEqual(TARGET_ORG, result.metadata["target_org"])
        self.assertEqual("sf_target_org_scope", result.metadata["target_evidence"])
        self.assertEqual(2, result.metadata["expected_component_count"])
        self.assertEqual(2, result.metadata["reported_component_count"])
        self.assertEqual(2, result.metadata["matched_component_count"])
        self.assertEqual(0, result.metadata["missing_component_count"])
        self.assertEqual(1, result.metadata["excluded_package_xml_count"])
        self.assertIn("expected=2, reported=2, matched=2", result.detail)
        for component_spec in EXPECTED_COMPONENTS:
            self.assertNotIn(component_spec, result.detail)

    def test_succeeded_validation_is_pass(self):
        with mock.patch.object(
            dispatcher.subprocess,
            "run",
            return_value=_completed(_payload(checkOnly=True)),
        ):
            result = dispatcher.dispatch_meta_api(
                TARGET_ORG,
                "validation",
                deploy_job_id=JOB_ID,
                deploy_components=EXPECTED_COMPONENTS,
            )

        self.assertEqual("PASS", result.status)
        self.assertEqual("validation", result.metadata["operation"])
        self.assertIn("validation", result.detail)

    def test_valid_manifest_alone_supplies_scope_and_sha256(self):
        manifest_text = _manifest_xml()
        expected_hash = "sha256:" + hashlib.sha256(
            manifest_text.encode("utf-8")
        ).hexdigest()
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "package.xml"
            manifest_path.write_text(manifest_text, encoding="utf-8")
            with mock.patch.object(
                dispatcher.subprocess,
                "run",
                return_value=_completed(_payload()),
            ):
                result = dispatcher.dispatch_meta_api(
                    TARGET_ORG,
                    "manifest-scoped validation",
                    deploy_job_id=JOB_ID,
                    deploy_manifest=str(manifest_path),
                )

        self.assertEqual("PASS", result.status)
        self.assertEqual(0, result.metadata["explicit_component_count"])
        self.assertEqual(2, result.metadata["manifest_component_count"])
        self.assertEqual(2, result.metadata["expected_component_count"])
        self.assertEqual(expected_hash, result.metadata["deploy_manifest_sha256"])
        self.assertIn(f"manifest_sha256={expected_hash}", result.detail)
        self.assertNotIn(str(manifest_path), result.detail)

    def test_manifest_and_explicit_components_are_unioned(self):
        manifest_text = _manifest_xml((EXPECTED_COMPONENTS[0],))
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "package.xml"
            manifest_path.write_text(manifest_text, encoding="utf-8")
            with mock.patch.object(
                dispatcher.subprocess,
                "run",
                return_value=_completed(_payload()),
            ):
                result = dispatcher.dispatch_meta_api(
                    TARGET_ORG,
                    "union-scoped validation",
                    deploy_job_id=JOB_ID,
                    deploy_components=(EXPECTED_COMPONENTS[1],),
                    deploy_manifest=str(manifest_path),
                )

        self.assertEqual("PASS", result.status)
        self.assertEqual(1, result.metadata["explicit_component_count"])
        self.assertEqual(1, result.metadata["manifest_component_count"])
        self.assertEqual(2, result.metadata["expected_component_count"])
        self.assertEqual(2, result.metadata["matched_component_count"])

    def test_expected_components_are_deduplicated_and_extra_reported_is_allowed(self):
        payload = _payload()
        payload["result"]["details"]["componentSuccesses"].append(
            _component_success("ApexClass:UnrelatedHelper")
        )
        duplicated_expected = (
            EXPECTED_COMPONENTS[0],
            EXPECTED_COMPONENTS[0],
            EXPECTED_COMPONENTS[1],
        )
        with mock.patch.object(
            dispatcher.subprocess,
            "run",
            return_value=_completed(payload),
        ):
            result = dispatcher.dispatch_meta_api(
                TARGET_ORG,
                "metadata change",
                deploy_job_id=JOB_ID,
                deploy_components=duplicated_expected,
            )

        self.assertEqual("PASS", result.status)
        self.assertEqual(2, result.metadata["expected_component_count"])
        self.assertEqual(3, result.metadata["reported_component_count"])
        self.assertEqual(2, result.metadata["matched_component_count"])

    def test_missing_or_malformed_report_component_evidence_is_error(self):
        package_entry = _payload()["result"]["details"]["componentSuccesses"][0]
        cases = (
            _payload(details=None),
            _payload(details={}),
            _payload(details={"componentSuccesses": {}}),
            _payload(details={"componentSuccesses": []}),
            _payload(details={"componentSuccesses": [package_entry]}),
            _payload(details={"componentSuccesses": ["not-an-object"]}),
            _payload(details={"componentSuccesses": [{
                "componentType": "Flow",
                "fullName": "Client_Intake",
                "success": "true",
            }]}),
        )
        for payload in cases:
            with self.subTest(details=payload["result"].get("details")):
                with mock.patch.object(
                    dispatcher.subprocess,
                    "run",
                    return_value=_completed(payload),
                ):
                    result = dispatcher.dispatch_meta_api(
                        TARGET_ORG,
                        "metadata change",
                        deploy_job_id=JOB_ID,
                        deploy_components=EXPECTED_COMPONENTS,
                    )
                self.assertEqual("ERROR", result.status)
                self.assertIn("artifact evidence", result.detail)
                self.assertEqual(
                    2,
                    result.metadata["expected_component_count"],
                )
                self.assertIsNone(result.metadata["reported_component_count"])

    def test_expected_component_absent_from_exact_job_is_fail(self):
        package_entry = _payload()["result"]["details"]["componentSuccesses"][0]
        payload = _payload(details={
            "componentSuccesses": [
                package_entry,
                _component_success(EXPECTED_COMPONENTS[0]),
                _component_success(
                    "CustomObject:Matter__c.Referral_Status__c"
                ),
            ],
        })
        with mock.patch.object(
            dispatcher.subprocess,
            "run",
            return_value=_completed(payload),
        ):
            result = dispatcher.dispatch_meta_api(
                TARGET_ORG,
                "metadata change",
                deploy_job_id=JOB_ID,
                deploy_components=EXPECTED_COMPONENTS,
            )

        self.assertEqual("FAIL", result.status)
        self.assertEqual(2, result.metadata["expected_component_count"])
        self.assertEqual(2, result.metadata["reported_component_count"])
        self.assertEqual(1, result.metadata["matched_component_count"])
        self.assertEqual(1, result.metadata["missing_component_count"])
        self.assertIn("expected=2, reported=2, matched=1, missing=1", result.detail)
        for component_spec in EXPECTED_COMPONENTS:
            self.assertNotIn(component_spec, result.detail)

    def test_expected_component_with_unsuccessful_evidence_is_fail(self):
        package_entry = _payload()["result"]["details"]["componentSuccesses"][0]
        payload = _payload(details={
            "componentSuccesses": [
                package_entry,
                _component_success(EXPECTED_COMPONENTS[0]),
                _component_success(EXPECTED_COMPONENTS[1], success=False),
            ],
        })
        with mock.patch.object(
            dispatcher.subprocess,
            "run",
            return_value=_completed(payload),
        ):
            result = dispatcher.dispatch_meta_api(
                TARGET_ORG,
                "metadata change",
                deploy_job_id=JOB_ID,
                deploy_components=EXPECTED_COMPONENTS,
            )

        self.assertEqual("FAIL", result.status)
        self.assertEqual(2, result.metadata["reported_component_count"])
        self.assertEqual(1, result.metadata["matched_component_count"])
        self.assertEqual(1, result.metadata["missing_component_count"])
        self.assertEqual(
            1,
            result.metadata["unsuccessful_reported_component_count"],
        )
        self.assertIn("unsuccessful=1", result.detail)

    def test_mismatched_reported_job_is_error(self):
        with mock.patch.object(
            dispatcher.subprocess,
            "run",
            return_value=_completed(_payload(id=OTHER_JOB_ID)),
        ):
            result = dispatcher.dispatch_meta_api(
                TARGET_ORG,
                "metadata change",
                deploy_job_id=JOB_ID,
                deploy_components=EXPECTED_COMPONENTS,
            )

        self.assertEqual("ERROR", result.status)
        self.assertIn("evidence mismatch", result.detail)
        self.assertEqual(OTHER_JOB_ID, result.metadata["reported_job_id"])

    def test_explicit_alias_target_mismatch_is_error(self):
        with mock.patch.object(
            dispatcher.subprocess,
            "run",
            return_value=_completed(_payload(targetOrgAlias="other-org")),
        ):
            result = dispatcher.dispatch_meta_api(
                TARGET_ORG,
                "metadata change",
                deploy_job_id=JOB_ID,
                deploy_components=EXPECTED_COMPONENTS,
            )

        self.assertEqual("ERROR", result.status)
        self.assertIn("target mismatch", result.detail)
        self.assertEqual("targetOrgAlias", result.metadata["reported_target_field"])

    def test_explicit_username_target_is_compared_when_username_was_requested(self):
        target_username = "qa@example.org"
        with mock.patch.object(
            dispatcher.subprocess,
            "run",
            return_value=_completed(_payload(targetUsername="wrong@example.org")),
        ):
            result = dispatcher.dispatch_meta_api(
                target_username,
                "metadata change",
                deploy_job_id=JOB_ID,
                deploy_components=EXPECTED_COMPONENTS,
            )

        self.assertEqual("ERROR", result.status)
        self.assertIn("target mismatch", result.detail)

    def test_pending_exact_job_is_manual_required(self):
        for status in ("Pending", "InProgress"):
            with self.subTest(status=status):
                with mock.patch.object(
                    dispatcher.subprocess,
                    "run",
                    return_value=_completed(
                        _payload(status=status, success=False, done=False)
                    ),
                ):
                    result = dispatcher.dispatch_meta_api(
                        TARGET_ORG,
                        "metadata change",
                        deploy_job_id=JOB_ID,
                        deploy_components=EXPECTED_COMPONENTS,
                    )
                self.assertEqual("MANUAL_REQUIRED", result.status)
                self.assertIn("not terminal", result.detail)

    def test_terminal_failed_or_canceled_job_is_fail(self):
        for status in ("Failed", "Canceled"):
            with self.subTest(status=status):
                with mock.patch.object(
                    dispatcher.subprocess,
                    "run",
                    return_value=_completed(
                        _payload(status=status, success=False, done=True),
                        returncode=0,
                    ),
                ):
                    result = dispatcher.dispatch_meta_api(
                        TARGET_ORG,
                        "metadata change",
                        deploy_job_id=JOB_ID,
                        deploy_components=EXPECTED_COMPONENTS,
                    )
                self.assertEqual("FAIL", result.status)
                self.assertIn("failed", result.detail)

    def test_contradictory_error_counts_refuse_pass(self):
        for field in ("numberComponentErrors", "numberTestErrors"):
            with self.subTest(field=field):
                with mock.patch.object(
                    dispatcher.subprocess,
                    "run",
                    return_value=_completed(_payload(**{field: 1})),
                ):
                    result = dispatcher.dispatch_meta_api(
                        TARGET_ORG,
                        "metadata change",
                        deploy_job_id=JOB_ID,
                        deploy_components=EXPECTED_COMPONENTS,
                    )
                self.assertEqual("FAIL", result.status)
                self.assertIn("contradictory PASS", result.detail)

    def test_incomplete_or_contradictory_success_shape_is_error(self):
        cases = (
            _payload(success=None),
            _payload(done=None),
            _payload(checkOnly=None),
            _payload(status="Completed"),
            {**_payload(), "status": 1},
            {**_payload(), "status": False},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                with mock.patch.object(
                    dispatcher.subprocess,
                    "run",
                    return_value=_completed(payload),
                ):
                    result = dispatcher.dispatch_meta_api(
                        TARGET_ORG,
                        "metadata change",
                        deploy_job_id=JOB_ID,
                        deploy_components=EXPECTED_COMPONENTS,
                    )
                self.assertEqual("ERROR", result.status)
                self.assertIn("not conclusive", result.detail)

    def test_malformed_error_counts_are_error(self):
        for field, value in (
            ("numberComponentErrors", "1"),
            ("numberTestErrors", -1),
            ("numberComponentErrors", True),
        ):
            with self.subTest(field=field, value=value):
                with mock.patch.object(
                    dispatcher.subprocess,
                    "run",
                    return_value=_completed(_payload(**{field: value})),
                ):
                    result = dispatcher.dispatch_meta_api(
                        TARGET_ORG,
                        "metadata change",
                        deploy_job_id=JOB_ID,
                        deploy_components=EXPECTED_COMPONENTS,
                    )
                self.assertEqual("ERROR", result.status)
                self.assertIn("malformed error counts", result.detail)

    def test_invalid_json_or_missing_result_is_error(self):
        cases = (
            _completed("not-json", returncode=0),
            _completed({"status": 0}, returncode=0),
            _completed("not-json", returncode=1, stderr="auth failed"),
        )
        for completed in cases:
            with self.subTest(stdout=completed.stdout, returncode=completed.returncode):
                with mock.patch.object(
                    dispatcher.subprocess,
                    "run",
                    return_value=completed,
                ):
                    result = dispatcher.dispatch_meta_api(
                        TARGET_ORG,
                        "metadata change",
                        deploy_job_id=JOB_ID,
                        deploy_components=EXPECTED_COMPONENTS,
                    )
                self.assertEqual("ERROR", result.status)

    def test_nonzero_exit_cannot_pass_even_with_succeeded_payload(self):
        with mock.patch.object(
            dispatcher.subprocess,
            "run",
            return_value=_completed(_payload(), returncode=1),
        ):
            result = dispatcher.dispatch_meta_api(
                TARGET_ORG,
                "metadata change",
                deploy_job_id=JOB_ID,
                deploy_components=EXPECTED_COMPONENTS,
            )

        self.assertEqual("ERROR", result.status)
        self.assertIn("exit=1", result.detail)

    def test_timeout_or_missing_cli_is_error(self):
        errors = (
            subprocess.TimeoutExpired(cmd=["sf"], timeout=dispatcher.TIMEOUT_MEDIUM),
            FileNotFoundError("sf"),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(
                    dispatcher.subprocess,
                    "run",
                    side_effect=error,
                ):
                    result = dispatcher.dispatch_meta_api(
                        TARGET_ORG,
                        "metadata change",
                        deploy_job_id=JOB_ID,
                        deploy_components=EXPECTED_COMPONENTS,
                    )
                self.assertEqual("ERROR", result.status)
                self.assertIn("Could not collect", result.detail)

    def test_dispatch_surface_forwards_job_only_to_meta_api(self):
        with mock.patch.object(
            dispatcher.subprocess,
            "run",
            return_value=_completed(_payload()),
        ):
            result = dispatcher.dispatch_surface(
                "MetaAPI",
                TARGET_ORG,
                "metadata change",
                deploy_job_id=JOB_ID,
                deploy_components=EXPECTED_COMPONENTS,
            )

        self.assertEqual("PASS", result.status)
        self.assertEqual(JOB_ID, result.metadata["requested_job_id"])


class MetaApiCliTests(unittest.TestCase):
    def test_parser_accepts_descriptive_and_sf_style_job_id_flags(self):
        parser = cli.build_parser()
        for flag in ("--deploy-job-id", "--job-id"):
            with self.subTest(flag=flag):
                args = parser.parse_args(
                    ["run", "A3 Flow change", "--org", TARGET_ORG, flag, JOB_ID]
                )
                self.assertEqual(JOB_ID, args.deploy_job_id)

    def test_parser_collects_repeatable_deploy_components(self):
        parser = cli.build_parser()
        args = parser.parse_args([
            "run", "A3 Flow change",
            "--org", TARGET_ORG,
            "--deploy-job-id", JOB_ID,
            "--deploy-component", EXPECTED_COMPONENTS[0],
            "--deploy-component", EXPECTED_COMPONENTS[1],
        ])
        self.assertEqual(list(EXPECTED_COMPONENTS), args.deploy_components)

    def test_parser_accepts_local_deploy_manifest(self):
        parser = cli.build_parser()
        args = parser.parse_args([
            "run", "A3 Flow change",
            "--org", TARGET_ORG,
            "--deploy-job-id", JOB_ID,
            "--deploy-manifest", "manifest/package.xml",
        ])
        self.assertEqual("manifest/package.xml", args.deploy_manifest)

    def test_cli_run_forwards_job_id_to_surface_dispatch(self):
        parser = cli.build_parser()
        args = parser.parse_args(
            [
                "run", "A3 Flow change",
                "--org", TARGET_ORG,
                "--deploy-job-id", JOB_ID,
                "--deploy-component", EXPECTED_COMPONENTS[0],
                "--deploy-component", EXPECTED_COMPONENTS[1],
                "--deploy-manifest", "manifest/package.xml",
            ]
        )
        change_type = {"id": "A3", "name": "Flow CRUD"}
        pass_result = dispatcher.DispatchResult(
            surface="MetaAPI",
            status="PASS",
            detail="exact evidence",
        )

        with (
            mock.patch.object(cli.router, "load_router", return_value={
                "change_types": [change_type],
            }),
            mock.patch.object(
                cli.router,
                "match_change_types_scored",
                return_value=[(change_type, 100)],
            ),
            mock.patch.object(
                cli.router,
                "required_surfaces",
                return_value={
                    "required_automated": ["MetaAPI"],
                    "required_manual": [],
                    "one_of": [],
                },
            ),
            mock.patch.object(cli, "_is_production_alias", return_value=False),
            mock.patch.object(cli, "_get_org_id_18", return_value=None),
            mock.patch.object(
                cli.dispatcher,
                "dispatch_surface",
                return_value=pass_result,
            ) as dispatch,
            mock.patch.object(cli.report, "format_report", return_value="report"),
        ):
            return_code = args.func(args)

        self.assertEqual(0, return_code)
        dispatch.assert_called_once_with(
            "MetaAPI",
            TARGET_ORG,
            "A3 Flow change",
            deploy_job_id=JOB_ID,
            deploy_components=list(EXPECTED_COMPONENTS),
            deploy_manifest="manifest/package.xml",
        )


class MetaApiRouterTests(unittest.TestCase):
    @staticmethod
    def _change_types_by_id():
        loaded_router = router.load_router()
        return loaded_router, {
            change_type["id"]: change_type
            for change_type in loaded_router["change_types"]
        }

    def test_data_only_b1_through_b4_do_not_require_metadata_deploy_proof(self):
        loaded_router, by_id = self._change_types_by_id()
        for change_type_id in ("B1", "B2", "B3", "B4"):
            with self.subTest(change_type_id=change_type_id):
                change_type = by_id[change_type_id]
                self.assertEqual(
                    "not_applicable",
                    change_type["surfaces"]["MetaAPI"],
                )
                grouped = router.required_surfaces(
                    change_type,
                    is_production=True,
                    router=loaded_router,
                )
                self.assertNotIn("MetaAPI", grouped["required_automated"])

        # B5 is Custom Metadata Type configuration and remains deploy-scoped.
        self.assertEqual(
            "required_automated",
            by_id["B5"]["surfaces"]["MetaAPI"],
        )

    def test_other_non_deploy_rows_do_not_require_metadata_deploy_proof(self):
        loaded_router, by_id = self._change_types_by_id()
        for change_type_id in ("A11", "F4", "F5", "G3", "H6"):
            with self.subTest(change_type_id=change_type_id):
                change_type = by_id[change_type_id]
                self.assertEqual(
                    "not_applicable",
                    change_type["surfaces"]["MetaAPI"],
                )
                grouped = router.required_surfaces(
                    change_type,
                    is_production=True,
                    router=loaded_router,
                )
                self.assertNotIn("MetaAPI", grouped["required_automated"])

    def test_metadata_and_mixed_deploy_rows_still_require_exact_deploy_proof(self):
        _, by_id = self._change_types_by_id()
        # A metadata row, a standard-deployment workflow, and a mixed
        # scheduled-Apex workflow all contain actual metadata deploy phases.
        for change_type_id in ("A3", "G6", "H5"):
            with self.subTest(change_type_id=change_type_id):
                self.assertEqual(
                    "required_automated",
                    by_id[change_type_id]["surfaces"]["MetaAPI"],
                )


if __name__ == "__main__":
    unittest.main()
