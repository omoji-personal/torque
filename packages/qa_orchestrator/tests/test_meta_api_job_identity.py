"""Deployment request identity must survive Salesforce's 15/18-ID response shape."""
import json
import subprocess
import unittest
from unittest import mock

from jsc_qa import dispatcher


# Synthetic fixtures; the suffixes encode uppercase positions in the first 15.
JOB15 = "0AfVs000001ojZF"
JOB18 = "0AfVs000001ojZFKAY"
OTHER15 = "0Af0x000017yLUF"
OTHER18 = "0Af0x000017yLUFCA2"
CASE_VARIANT15 = "0Afvs000001ojZF"
CASE_VARIANT18 = "0Afvs000001ojZFCAY"


def report(job):
    return subprocess.CompletedProcess([], 0, json.dumps({
        "status": 0,
        "result": {"statusCode": 200, "headers": {}, "body": {"deployResult": {
            "id": job, "status": "Succeeded", "done": True, "success": True,
            "checkOnly": False, "numberComponentErrors": 0, "numberTestErrors": 0,
            "details": {"componentSuccesses": [{
                "componentType": "Flow", "fullName": "Expected", "success": True,
            }]},
        }}},
    }), "")


def dispatch(job):
    return dispatcher.dispatch_meta_api("synthetic-org", "metadata change",
                                       deploy_job_id=job, deploy_components=["Flow:Expected"])


class MetaApiJobIdentityTests(unittest.TestCase):
    def test_valid_representations_match_and_preserve_original_identifiers(self):
        for requested in (JOB15, JOB18):
            for returned in (JOB15, JOB18):
                with self.subTest(requested=requested, returned=returned):
                    with mock.patch.object(dispatcher.subprocess, "run",
                                           return_value=report(returned)) as run:
                        result = dispatch(requested)
                    self.assertEqual(result.status, "PASS")
                    self.assertEqual(result.metadata["requested_job_id"], requested)
                    self.assertEqual(result.metadata["reported_job_id"], returned)
                    self.assertEqual(json.loads(result.raw_output)["result"]["id"], returned)
                    self.assertIn(f"deployRequest/{requested}?includeDetails=true", run.call_args.args[0][4])

    def test_valid_different_job_is_rejected_in_both_representations(self):
        for requested in (JOB15, JOB18):
            for returned in (OTHER15, OTHER18):
                with self.subTest(requested=requested, returned=returned):
                    with mock.patch.object(dispatcher.subprocess, "run", return_value=report(returned)):
                        self.assertEqual(dispatch(requested).status, "ERROR")

    def test_case_sensitive_identity_rejects_distinct_valid_ids(self):
        for requested in (JOB15, JOB18):
            for returned in (CASE_VARIANT15, CASE_VARIANT18):
                with self.subTest(requested=requested, returned=returned):
                    with mock.patch.object(dispatcher.subprocess, "run", return_value=report(returned)):
                        self.assertEqual(dispatch(requested).status, "ERROR")

    def test_case_variant_is_valid_for_itself(self):
        with mock.patch.object(dispatcher.subprocess, "run", return_value=report(CASE_VARIANT18)):
            self.assertEqual(dispatch(CASE_VARIANT15).status, "PASS")

    def test_invalid_requested_id_never_invokes_provider(self):
        invalid = (
            JOB15 + "AAA", JOB15 + "kay", JOB15 + "KAZ", JOB15 + "KA6",
            "0Afvs000001ojZFKAY", "0afVs000001ojZFKAY", "0AFVs000001ojZFKAY",
            "0AfVs000001ojZＦKAY", "0AfVs000001ojZF\u200b", JOB18 + "\n",
            " " + JOB15, JOB15 + " ", JOB15 + "K", JOB15 + "KA", JOB18 + "X",
            JOB15.encode(), True, [], {},
        )
        for value in invalid:
            with self.subTest(value=value):
                with mock.patch.object(dispatcher.subprocess, "run") as run:
                    result = dispatch(value)
                self.assertEqual(result.status, "ERROR")
                self.assertIn("Invalid deploy job ID", result.detail)
                run.assert_not_called()

    def test_invalid_reported_id_never_truncates_or_normalizes_to_match(self):
        invalid = (
            JOB15 + "AAA", JOB15 + "kay", JOB15 + "KAZ", JOB15 + "KA6",
            "0Afvs000001ojZFKAY", "0afVs000001ojZFKAY", "0AFVs000001ojZFKAY",
            "0AfVs000001ojZＦKAY", "0AfVs000001ojZF\u200b", JOB18 + "\n",
            " " + JOB15, JOB15 + " ", JOB15 + "K", JOB15 + "KA", JOB18 + "X",
            None, True, [], {}, "",
        )
        for value in invalid:
            with self.subTest(value=value):
                with mock.patch.object(dispatcher.subprocess, "run", return_value=report(value)):
                    result = dispatch(JOB15)
                self.assertEqual(result.status, "ERROR")
                self.assertEqual(result.metadata["reported_job_id"], value)


if __name__ == "__main__":
    unittest.main()
