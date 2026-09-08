import pytest
from jsc_qa.dispatcher import DispatchResult
from jsc_qa.report import format_report, result_exit_code

@pytest.mark.parametrize('statuses,expected', [([],3),(['DEFERRED'],3),(['PASS','DEFERRED'],3),(['MANUAL_REQUIRED'],3),(['SKIP_VIA_TOKEN'],3),(['UNKNOWN'],3),(['PASS'],0),(['PASS','PASS'],0),(['FAIL'],1),(['PASS','ERROR'],1)])
def test_only_actual_full_coverage_reports_pass(statuses, expected):
    results = [DispatchResult('Example', status, 'Synthetic fixture') for status in statuses]
    assert result_exit_code(results) == expected
    text = format_report([], 'synthetic-dev', False, results)
    assert ('VERDICT: All requested verification surfaces PASSED.' in text) is (expected == 0)
    assert ('VERDICT: INCOMPLETE.' in text) is (expected == 3)

def test_explicit_skips_do_not_disappear_from_aggregate():
    results = [DispatchResult('Example', 'PASS', 'Synthetic fixture')]
    assert result_exit_code(results, skipped=True) == 3
    assert 'INCOMPLETE' in format_report([], 'synthetic-dev', False, results, [('Other', 'explicit skip')])
