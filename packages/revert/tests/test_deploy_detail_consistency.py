"""Supplied metadata-job details must not contradict a success summary."""
import pytest

from jsc_revert.job_outcomes import deploy_outcome

JOB = '0Af000000000001AAA'


def successful(**changes):
    result = {'id': JOB, 'status': 'Succeeded', 'done': True, 'success': True,
              'numberComponentErrors': 0, 'numberTestErrors': 0,
              'numberTestsCompleted': 0, 'numberTestsTotal': 0}
    result.update(changes)
    return {'result': result}


@pytest.mark.parametrize('details', [
    None, {}, {'componentFailures': None}, {'componentFailures': []},
    {'runTestResult': None}, {'runTestResult': {}},
    {'runTestResult': {'failures': [], 'numFailures': 0, 'numTestsRun': 0}},
    {'runTestResult': {'numFailures': 0, 'numberFailures': 0}},
])
def test_optional_or_consistent_details_preserve_success(details):
    assert deploy_outcome(successful(details=details)) == ('complete', JOB)


def test_minimal_summary_response_still_supported():
    assert deploy_outcome({'result': {'id': JOB, 'status': 'Succeeded', 'done': True,
                                     'success': True}}) == ('complete', JOB)


@pytest.mark.parametrize('details', [
    {'componentFailures': [{'success': False, 'problem': 'Synthetic failure'}]},
    {'componentFailures': {'success': False, 'problem': 'Synthetic singleton failure'}},
    {'runTestResult': {'failures': [{'message': 'Synthetic test failure'}]}},
    {'runTestResult': {'numFailures': 1, 'failures': []}},
    {'runTestResult': {'numberFailures': 1, 'failures': []}},
    {'runTestResult': {'numFailures': 0, 'numberFailures': 1}},
    {'runTestResult': {'numTestsRun': 1}},
])
def test_contradictory_details_cannot_establish_completion(details):
    assert deploy_outcome(successful(details=details)) == ('partial', JOB)


@pytest.mark.parametrize('details', [
    [], 'malformed', {'componentFailures': False}, {'componentFailures': {}},
    {'runTestResult': []}, {'runTestResult': 'malformed'},
    {'runTestResult': {'failures': False}},
])
def test_malformed_present_details_cannot_establish_completion(details):
    assert deploy_outcome(successful(details=details)) == ('partial', JOB)


@pytest.mark.parametrize('key', ['numFailures', 'numberFailures', 'numTestsRun'])
@pytest.mark.parametrize('value', [True, False, -1, '0', 0.0])
def test_present_nested_counts_keep_integer_types(key, value):
    assert deploy_outcome(successful(details={'runTestResult': {key: value}})) == ('partial', JOB)


def test_completed_nonzero_tests_and_summary_count_encoding_supported():
    result = successful(numberTestsCompleted='2', numberTestsTotal=2,
                        details={'runTestResult': {'numTestsRun': 2, 'numFailures': 0,
                                                   'failures': []}})
    assert deploy_outcome(result) == ('complete', JOB)


@pytest.mark.parametrize('completed,total,ran', [(1, 2, 1), (2, 2, 1), (2, 1, 2)])
def test_supplied_completion_counts_must_agree(completed, total, ran):
    result = successful(numberTestsCompleted=completed, numberTestsTotal=total,
                        details={'runTestResult': {'numTestsRun': ran, 'numFailures': 0}})
    assert deploy_outcome(result) == ('partial', JOB)


def test_optional_missing_summary_counts_do_not_erase_consistent_details():
    result = successful(details={'runTestResult': {'numTestsRun': 2, 'numFailures': 0}})
    result['result'].pop('numberTestsCompleted')
    result['result'].pop('numberTestsTotal')
    assert deploy_outcome(result) == ('complete', JOB)


def test_secondary_cli_container_failure_is_not_ignored():
    result = successful()
    result['data'] = {'details': {'runTestResult': {'numFailures': 1}}}
    assert deploy_outcome(result) == ('partial', JOB)


def test_rest_wrapped_detail_failure_retains_expected_job_identity():
    result = successful(details={'componentFailures': [{'problem': 'Synthetic failure'}]})
    wrapped = {'result': {'statusCode': 200, 'body': result['result']}}
    assert deploy_outcome(wrapped, expected_job_id=JOB) == ('partial', JOB)


def test_failure_and_pending_statuses_keep_their_existing_classification():
    assert deploy_outcome(successful(status='Failed', success=False,
        details={'componentFailures': [{'problem': 'Expected failure'}]})) == ('failed', JOB)
    assert deploy_outcome(successful(status='InProgress', done=False,
        details={'runTestResult': {'numTestsRun': 1}})) == ('pending_finalize_required', JOB)
