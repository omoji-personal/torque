"""Synthetic transport tests: no Salesforce/auth interactions are permitted."""
import json
import subprocess
import unittest
from unittest import mock

from jsc_qa import dispatcher

JOB = "0AfVs000001ojZFKAY"
OTHER_JOB = "0Af0x000017yLUFCA2"
TARGET = "synthetic-client-a"


def deploy_result(**overrides):
    result = {
        "id": JOB, "status": "Succeeded", "done": True, "success": True,
        "checkOnly": False, "numberComponentErrors": 0, "numberTestErrors": 0,
        "details": {"componentSuccesses": [{
            "componentType": "Flow", "fullName": "Expected", "success": True,
        }]},
    }
    result.update(overrides)
    return result


def transport(body=None, *, code=200, status=0, headers=None, exit_code=0):
    return subprocess.CompletedProcess([], exit_code, json.dumps({
        "status": status,
        "result": {"statusCode": code, "headers": headers or {},
                   "body": body if body is not None else {"deployResult": deploy_result()}},
    }), "")


def dispatch():
    return dispatcher.dispatch_meta_api(TARGET, "synthetic metadata change",
                                       deploy_job_id=JOB, deploy_components=["Flow:Expected"])


class MetaApiTransportTests(unittest.TestCase):
    def test_foreign_cached_job_cannot_certify_selected_client(self):
        def fake_cli(command, **kwargs):
            # Reproduce the old report command's cache override: the request ID
            # and component match, and the foreign report emits no org identity.
            if command[1:4] == ["project", "deploy", "report"]:
                return subprocess.CompletedProcess(command, 0, json.dumps({
                    "status": 0, "result": deploy_result(),
                }), "")
            self.assertEqual(command[1:4], ["api", "request", "rest"])
            self.assertEqual(command[command.index("--target-org") + 1], TARGET)
            return transport([{"errorCode": "INVALID_CROSS_REFERENCE_KEY"}], code=404,
                             exit_code=1)

        with mock.patch.object(dispatcher.subprocess, "run", side_effect=fake_cli) as run:
            result = dispatch()
        self.assertEqual(result.status, "ERROR")
        self.assertEqual(run.call_count, 1)

    def test_http_success_and_nested_failed_is_fail_even_with_exit_zero(self):
        report = deploy_result(status="Failed", done=True, success=False)
        with mock.patch.object(dispatcher.subprocess, "run",
                               return_value=transport({"deployResult": report})):
            result = dispatch()
        self.assertEqual(result.status, "FAIL")
        self.assertEqual(result.metadata["reported_job_id"], JOB)

    def test_wrong_job_is_rejected_even_with_http_success(self):
        with mock.patch.object(dispatcher.subprocess, "run", return_value=transport(
                {"deployResult": deploy_result(id=OTHER_JOB)})):
            result = dispatch()
        self.assertEqual(result.status, "ERROR")
        self.assertEqual(result.metadata["reported_job_id"], OTHER_JOB)

    def test_http_failure_cannot_become_pass_or_claimed_deploy_failure(self):
        for code in (301, 401, 403, 404, 429, 500, True, "200", None):
            for report in (deploy_result(), deploy_result(status="Failed", success=False)):
                with self.subTest(code=code, deploy_status=report["status"]):
                    with mock.patch.object(dispatcher.subprocess, "run", return_value=transport(
                            {"deployResult": report}, code=code)):
                        result = dispatch()
                    self.assertEqual(result.status, "ERROR")
                    self.assertEqual(result.raw_output, "")

    def test_malformed_body_is_error(self):
        for body in ("not-json", [], {}, {"deployResult": None}, {"deployResult": []}):
            with self.subTest(body=body):
                with mock.patch.object(dispatcher.subprocess, "run", return_value=transport(body)):
                    self.assertEqual(dispatch().status, "ERROR")

    def test_transport_failure_never_uses_successful_deploy_body(self):
        for status, exit_code in ((1, 0), (False, 0), (0, 1), (0, -9)):
            with self.subTest(status=status, exit_code=exit_code):
                with mock.patch.object(dispatcher.subprocess, "run", return_value=transport(
                        status=status, exit_code=exit_code)):
                    self.assertEqual(dispatch().status, "ERROR")

    def test_receipt_retains_legacy_shape_without_transport_headers(self):
        sentinel = "synthetic-cookie-must-not-be-retained"
        with mock.patch.object(dispatcher.subprocess, "run", return_value=transport(
                headers={"set-cookie": sentinel})):
            result = dispatch()
        self.assertEqual(result.status, "PASS")
        self.assertEqual(json.loads(result.raw_output), {"status": 0, "result": deploy_result()})
        self.assertNotIn(sentinel, repr(result))
        self.assertEqual(result.metadata["report_transport"], "sf_api_request_rest")
        self.assertEqual(result.metadata["report_http_status"], 200)

    def test_malformed_envelope_cannot_leak_arbitrary_content(self):
        sentinel = "synthetic-secret-in-invalid-status"
        with mock.patch.object(dispatcher.subprocess, "run", return_value=transport(
                status={"set-cookie": sentinel})):
            result = dispatch()
        self.assertEqual(result.status, "ERROR")
        self.assertNotIn(sentinel, repr(result))


if __name__ == "__main__":
    unittest.main()
