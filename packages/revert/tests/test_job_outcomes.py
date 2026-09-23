"""Real CLI envelope contracts and adversarial missing-evidence regressions."""
import json
import os
from types import SimpleNamespace

import pytest

from jsc_revert.job_outcomes import bulk_outcome, deploy_outcome
from jsc_revert import post_deploy_polling as poll
from jsc_revert.wrappers import _common as common
from jsc_revert.wrappers.data_bulk_import import _ids_from_success_csv

BULK = '750000000000001AAA'
DEPLOY = '0Af000000000001AAA'


@pytest.mark.parametrize('data', [None, [], '', 3, {}, {'result': None}, {'result': []}, {'result': {}}])
@pytest.mark.parametrize('code', [0, 1])
def test_missing_response_cannot_establish_success(data, code):
    assert bulk_outcome(data, code)[0] == 'partial'
    assert deploy_outcome(data, code)[0] == 'partial'


@pytest.mark.parametrize('processed,failed,expected', [(3, 0, 'complete'), (3, 1, 'applied_partial'), (3, 3, 'failed')])
def test_current_cli_flat_counts(processed, failed, expected):
    data = {'result': {'jobId': BULK, 'processedRecords': processed,
                       'failedRecords': failed, 'successfulRecords': processed-failed}}
    assert bulk_outcome(data) == (expected, BULK)


@pytest.mark.parametrize('changes', [
    {'processedRecords': -1}, {'failedRecords': 4}, {'failedRecords': True},
    {'successfulRecords': 1}, {'numberRecordsProcessed': 4}, {'jobId': 'bad'},
    {'id': '750000000000002AAA'}, {'state': 'MadeUp'},
])
def test_contradictory_or_invalid_counts_never_pass(changes):
    result = {'jobId': BULK, 'processedRecords': 3, 'failedRecords': 1, 'successfulRecords': 2}
    result.update(changes)
    assert bulk_outcome({'result': result})[0] == 'partial'


def test_current_cli_error_data_preserves_submitted_identity():
    data = {'status': 1, 'name': 'BulkJobError', 'data': {'jobId': BULK, 'state': 'JobComplete'}}
    assert bulk_outcome(data, 1) == ('pending_finalize_required', BULK)
    assert bulk_outcome({'result': {'jobId': BULK}}, 0) == ('pending_finalize_required', BULK)


def test_deploy_requires_affirmative_finished_success():
    result = {'id': DEPLOY, 'status': 'Succeeded', 'done': True, 'success': True}
    assert deploy_outcome({'result': result}) == ('complete', DEPLOY)
    for changes in ({'done': False}, {'success': False}, {'numberComponentErrors': 1}, {'numberTestErrors': 1}):
        assert deploy_outcome({'result': {**result, **changes}})[0] == 'partial'
    assert deploy_outcome({'result': {**result, 'status': 'SucceededPartial'}})[0] == 'applied_partial'


def test_partial_error_fetches_exact_job_without_resubmitting(tmp_path, monkeypatch):
    calls = []
    report = {'result': {'status': 'JobComplete', 'processedRecords': 3,
                         'failedRecords': 1, 'successfulRecords': 2}}
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return 0, json.dumps(report), ''
    monkeypatch.setattr(common, 'run_sf_subprocess', run)
    ctx = SimpleNamespace(org=SimpleNamespace(alias='synthetic'), snap_dir=tmp_path, manifest={'payload': {}})
    original = {'status': 1, 'data': {'jobId': BULK, 'state': 'JobComplete'}}
    assert common.capture_bulk_outcome(ctx, original, 1) == ('applied_partial', common.EXIT_SUCCEEDED_PARTIAL, BULK)
    assert len(calls) == 1
    assert calls[0][0] == ['sf', 'data', 'bulk', 'results', '--target-org', 'synthetic', '--job-id', BULK, '--json']
    assert calls[0][1]['cwd'] == str(tmp_path)
    artifact = json.loads((tmp_path/'bulk-result-observation.json').read_text(encoding="utf-8"))
    assert json.loads(artifact['stdout']) == report
    # POSIX only; Windows has no equivalent mode bits.
    assert os.name == "nt" or (tmp_path/'bulk-result-observation.json').stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('failed,expected', [(0, 'complete'), (1, 'applied_partial'), (3, 'failed')])
def test_polling_nonzero_structured_results_preserve_partial_status(tmp_path, monkeypatch, failed, expected):
    manifest = {'snapshot_status': 'pending_finalize_required'}
    monkeypatch.setattr(poll.mf, 'load_by_id', lambda *a: (tmp_path, manifest))
    monkeypatch.setattr(poll.mf, 'save', lambda directory, value: manifest.update(value))
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        assert kwargs['cwd'] == tmp_path
        data = {'result': {'id': BULK, 'state': 'JobComplete', 'numberRecordsProcessed': 3, 'numberRecordsFailed': failed}}
        return SimpleNamespace(returncode=1, stdout=json.dumps(data), stderr='retained diagnostic')
    monkeypatch.setattr('subprocess.run', run)
    entry = dict(org_id_short='synthetic', alias='synthetic', snapshot_id='synthetic', job_id=BULK,
                 operation_type='data_bulk_update', sf_report_command=['sf', 'data', 'update', 'resume'],
                 queue_entry_id='synthetic', retry_count=1)
    assert poll._poll_one(entry) == expected
    poll._update_snapshot_manifest(entry, expected)
    assert manifest['snapshot_status'] == expected
    assert len(calls) == 1
    assert len(manifest['post_deploy_polling']['observation_paths']) == 1


def test_poll_partial_is_terminal_without_becoming_completed(tmp_path, monkeypatch):
    monkeypatch.setattr(poll, '_poll_one', lambda e: 'applied_partial')
    updates = []
    monkeypatch.setattr(poll, '_update_snapshot_manifest', lambda entry, status: updates.append(status))
    poll.enqueue('snapshot', 'org', 'synthetic', 'data_bulk_update', BULK, ['sf'], queue_dir=tmp_path)
    path = tmp_path/'queue.jsonl'
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry['next_poll_at'] = '2000-01-01T00:00:00Z'
    path.write_text(json.dumps(entry)+'\n', encoding="utf-8")
    stats = poll.poll_once(tmp_path)
    assert stats['partial'] == 1 and stats['completed'] == 0
    assert updates == ['applied_partial']
    assert poll.poll_once(tmp_path)['polled'] == 0


def test_success_csv_uses_current_salesforce_header(tmp_path):
    path = tmp_path/'success.csv'
    path.write_text('sf__Id,sf__Created,Name\n001000000000001AAA,true,Synthetic\n', encoding="utf-8")
    assert _ids_from_success_csv(path) == ['001000000000001AAA']


@pytest.mark.parametrize('line_end,expected', [(b'\n','LF'),(b'\r\n','CRLF')])
def test_csv_header_line_ending_preserves_multiline_payload(tmp_path, line_end, expected):
    path=tmp_path/'input.csv'
    original=b'Name,Note__c'+line_end+b'Synthetic,"line1\nline2"'+line_end
    path.write_bytes(original)
    assert common.csv_line_ending(path)==expected
    assert path.read_bytes()==original


def test_boolean_integer_agreement_cannot_forge_deploy_success():
    data={'result': {'id': DEPLOY,'status':'Succeeded','done':True,'success':True},
          'data': {'done':1,'success':1}}
    assert deploy_outcome(data)[0]=='partial'


def test_failed_bulk_with_confirmed_successful_rows_is_partial_application():
    data={'result':{'id':BULK,'state':'Failed','processedRecords':3,'failedRecords':1,'successfulRecords':2}}
    assert bulk_outcome(data)==('applied_partial',BULK)


def test_actions_only_id_requires_recognized_context_and_exact_unique_identity():
    data={'name':'JobFailedError','context':'DataImportBulk',
          'actions':[f'Get results: "sf data bulk results -o untrusted-target --job-id {BULK}".']}
    assert bulk_outcome(data,1)==('pending_finalize_required',BULK)
    assert bulk_outcome({**data,'context':'Unrelated'},1)==('partial',None)
    data['actions'].append('"sf data bulk results --job-id 750000000000002AAA"')
    assert bulk_outcome(data,1)==('partial',None)


def test_exact_rest_failed_upload_has_zero_applied_rows():
    data={'result':{'statusCode':200,'body':{'id':BULK,'state':'Failed',
                        'numberRecordsProcessed':0,'numberRecordsFailed':0}}}
    assert bulk_outcome(data)==('failed',BULK)
    data['result']['statusCode']=500
    assert bulk_outcome(data)[0]!='complete'


def test_missing_poll_snapshot_cannot_launch_or_tombstone(monkeypatch):
    monkeypatch.setattr(poll.mf,'load_by_id',lambda *a: (_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setattr('subprocess.run',lambda *a,**k: pytest.fail('Missing capture launched a process'))
    entry=dict(org_id_short='x',alias='x',snapshot_id='x',operation_type='data_bulk_update',job_id=BULK,sf_report_command=['sf'])
    assert poll._poll_one(entry)=='pending'


def test_poll_timeout_retains_both_streams_without_false_completion(tmp_path,monkeypatch):
    import subprocess
    monkeypatch.setattr(poll.mf,'load_by_id',lambda *a:(tmp_path,{}))
    error=subprocess.TimeoutExpired(['sf'],60,output=b'partial-output',stderr=b'original-error')
    monkeypatch.setattr(subprocess,'run',lambda *a,**k: (_ for _ in ()).throw(error))
    entry=dict(org_id_short='x',alias='x',snapshot_id='x',operation_type='data_bulk_update',job_id=BULK,sf_report_command=['sf'])
    assert poll._poll_one(entry)=='pending'
    artifact=json.loads(next(tmp_path.glob('poll-*.json')).read_text(encoding="utf-8"))
    assert artifact['stdout']=='partial-output' and artifact['stderr']=='original-error'


def test_timeout_stdout_cannot_alone_establish_completion():
    assert deploy_outcome({'result':{'id':DEPLOY,'status':'Succeeded','done':True,'success':True}},-1)[0]=='pending_finalize_required'
    assert bulk_outcome({'result':{'id':BULK,'state':'JobComplete','processedRecords':3,'failedRecords':0}},-1)[0]=='pending_finalize_required'


def test_rest_conflicting_identity_or_failed_http_cannot_be_complete():
    body={'id':BULK,'state':'JobComplete','processedRecords':3,'failedRecords':0}
    assert bulk_outcome({'result':{'statusCode':200,'body':body},'data':{'jobId':'750000000000002AAA'}})[0]=='partial'
    assert bulk_outcome({'result':{'statusCode':500,**body}})[0]=='partial'


def test_unfinished_deploy_failure_cannot_be_terminal():
    assert deploy_outcome({'result':{'id':DEPLOY,'status':'Failed','done':False,'success':False}})[0]=='partial'


@pytest.mark.parametrize('module_name',['data_bulk_import','data_bulk_upsert'])
@pytest.mark.parametrize('data',[None,[],{'result':[]},{'result':{'successFilePath':True}}])
def test_missing_or_malformed_csv_locator_is_not_captured(tmp_path,monkeypatch,module_name,data):
    import importlib
    module=importlib.import_module('jsc_revert.wrappers.'+module_name)
    monkeypatch.setattr(module.c,'run_sf_subprocess',lambda *a,**k:(0,json.dumps(data),''))
    assert module._fetch_bulk_results('synthetic',BULK,tmp_path/'success.csv') is False
    assert not (tmp_path/'success.csv').exists()


@pytest.mark.parametrize('module_name',['data_bulk_import','data_bulk_upsert'])
def test_results_locator_cannot_copy_an_unrelated_private_file(tmp_path,monkeypatch,module_name):
    import importlib
    module=importlib.import_module('jsc_revert.wrappers.'+module_name)
    private=tmp_path/'private';private.mkdir();outside=tmp_path/'unrelated.txt';outside.write_text('unrelated', encoding="utf-8")
    data={'result':{'successFilePath':str(outside)}}
    monkeypatch.setattr(module.c,'run_sf_subprocess',lambda *a,**k:(0,json.dumps(data),''))
    assert module._fetch_bulk_results('synthetic',BULK,private/'success.csv') is False
