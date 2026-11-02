"""Round 2 contracts based on CLI 2.150.6, deploy plugin 4.1.2 and plugin-user.

All responses are synthetic. No test invokes a live org.
"""
import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from uuid import uuid4
from jsc_revert import org_detect, post_deploy_polling, revert_capabilities, snapshot_pre, stale_detector, tool_launch
from jsc_revert.cli import build_parser
from jsc_revert.wrappers import _bulk_csv, _common as c, apex_run, deploy, deploy_quick
from jsc_revert.wrappers import data_bulk_update as update, data_bulk_upsert as upsert
from jsc_revert.wrappers import org_assign_permset, org_assign_permsetlicense

JOB = '0Af000000000001AAA'
QUEUED = {'status': 0, 'result': {'id': JOB, 'done': False, 'status': 'Queued', 'files': []}}
TIMEOUT = {'name': 'ClientTimeoutError', 'message': 'The deployment is still running.',
           'exitCode': 1, 'status': 1, 'data': {'id': JOB}}
SUCCESS = {'result': {'id': JOB, 'status': 'Succeeded', 'done': True, 'success': True}}
IN_PROGRESS = {'result': {'id': JOB, 'status': 'InProgress', 'done': False, 'success': False}}


@pytest.fixture
def context(tmp_path, monkeypatch):
    root = tmp_path.parent / ('r2-' + uuid4().hex[:6])
    monkeypatch.setenv('TORQUE_WORKSPACE', str(root))
    monkeypatch.setenv('TORQUE_ORG_LOCK_DIR', str(tmp_path / 'locks'))
    info = org_detect.OrgInfo('synthetic-org', '00D000000000001AAA', '00D000000000001', False,
                             'https://example.invalid', 'https://login.salesforce.com', 'production')
    monkeypatch.setattr(org_detect, 'resolve_org', lambda *a, **kw: info)
    monkeypatch.setattr(c.mf, 'get_sf_cli_version', lambda: 'synthetic')
    return root


def manifest(root):
    return json.loads(next(root.rglob('manifest.json')).read_text(encoding='utf-8'))


def response(body, code=0):
    return code, json.dumps(body), ''


def before(monkeypatch):
    def retrieve(selectors, target, out, **kw):
        path = out / 'flows' / 'Synthetic.flow-meta.xml'
        path.parent.mkdir(parents=True)
        path.write_text('<Flow/>', encoding='utf-8')
        return snapshot_pre.RetrieveResult('Succeeded', [snapshot_pre.FileClassification(
            'Flow', 'Synthetic', 'present', 'a' * 64, str(path), None)], out / 'raw.json')
    monkeypatch.setattr(snapshot_pre, 'run_pre_snapshot_retrieve', retrieve)


@pytest.mark.parametrize('async_mode', [False, True])
def test_real_pending_shapes_queue_exact_job(context, monkeypatch, capsys, async_mode):
    before(monkeypatch)
    calls = []
    def run(cmd, **kw):
        calls.append((cmd, kw))
        if 'start' in cmd:
            return response(QUEUED)
        saved = manifest(context)
        assert saved['payload']['deploy_id'] == JOB  # durable before waiting
        assert saved['revert_capabilities']['automatic_revertible'] is False
        # report --wait catches polling expiry and returns current status (exit 0).
        return response(IN_PROGRESS)
    monkeypatch.setattr(c, 'run_sf_subprocess', run)
    args = build_parser().parse_args(['deploy', '-o', 'synthetic-org', '--metadata', 'Flow:Synthetic'] + (['--async'] if async_mode else []))
    assert deploy.run(args) == c.EXIT_IN_PROGRESS
    saved = manifest(context)
    assert saved['payload']['deploy_id'] == JOB
    assert saved['payload']['recovery_readiness'] == 'unknown'
    assert not revert_capabilities.effective_capabilities(saved)['automatic_revertible']
    assert '--async' in calls[0][0] and '--wait' not in calls[0][0]
    assert len(calls) == (1 if async_mode else 2)
    entries = post_deploy_polling._load_entries(next(context.rglob('queue.jsonl')))
    assert entries[0]['job_id'] == JOB and JOB in entries[0]['sf_report_command']
    assert 'may still be running' in capsys.readouterr().err
    # Poll the real queued snapshot through report and preserve unknown readiness.
    monkeypatch.setattr(post_deploy_polling, '_iso_now', lambda **kw: '2099-01-01T00:00:00Z')
    monkeypatch.setattr(tool_launch, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps(SUCCESS), stderr=''))
    assert post_deploy_polling.poll_once()['completed'] == 1
    saved = manifest(context)
    assert saved['snapshot_status'] == 'complete'
    assert saved['payload']['recovery_readiness'] == 'unknown'
    assert not saved['revert_capabilities']['automatic_revertible']


@pytest.mark.parametrize('wait', [33, 60])
def test_start_async_then_wait_under_lease_and_capture(context, monkeypatch, wait):
    before(monkeypatch)
    calls = []
    def run(cmd, **kw):
        calls.append((cmd, kw))
        if 'start' in cmd:
            assert '--async' in cmd
            return response(QUEUED)
        assert manifest(context)['payload']['deploy_id'] == JOB
        assert '--job-id' in cmd and JOB in cmd
        assert cmd[cmd.index('--wait') + 1] == str(wait)
        assert kw['timeout_seconds'] >= wait * 60 + 600
        return response(SUCCESS)
    def capture(path, saved, target):
        saved['payload']['after_state'] = {'captured': True}
    monkeypatch.setattr(c, 'run_sf_subprocess', run)
    monkeypatch.setattr(stale_detector, 'capture_deployment_state', capture)
    flags = [] if wait == 33 else ['--wait', str(wait)]
    assert deploy.run(build_parser().parse_args(['deploy', '-o', 'synthetic-org', '--metadata', 'Flow:Synthetic'] + flags)) == 0
    saved = manifest(context)
    assert saved['payload']['after_state']
    assert saved['payload']['recovery_readiness'] != 'unknown'
    assert saved['revert_capabilities']['automatic_revertible']
    assert len(calls) == 2


def test_capture_failure_downgrades_label(context, monkeypatch):
    before(monkeypatch)
    monkeypatch.setattr(c, 'run_sf_subprocess', lambda *a, **k: response(SUCCESS))
    def fail(*a):
        raise ValueError('synthetic retrieve failure')
    monkeypatch.setattr(stale_detector, 'capture_deployment_state', fail)
    assert deploy.run(build_parser().parse_args(['deploy', '-o', 'synthetic-org', '--metadata', 'Flow:Synthetic'])) == c.EXIT_POST_FINALIZE_FAILED
    assert not revert_capabilities.effective_capabilities(manifest(context))['automatic_revertible']


def test_quick_timeout_retains_job(context, monkeypatch):
    promoted = '0Af000000000002AAA'
    monkeypatch.setattr(c, 'run_sf_subprocess', lambda *a, **kw: response({**TIMEOUT, 'data': {'id': promoted}}, 1))
    assert deploy_quick.run(build_parser().parse_args(['deploy-quick', '-o', 'synthetic-org', '--job-id', JOB])) == c.EXIT_IN_PROGRESS
    assert manifest(context)['payload']['deploy_id'] == promoted
    entries = post_deploy_polling._load_entries(next(context.rglob('queue.jsonl')))
    assert entries[0]['job_id'] == promoted and promoted in entries[0]['sf_report_command']
    assert manifest(context)['payload']['report_cwd'] == str(Path.cwd())
    monkeypatch.setattr(post_deploy_polling, '_iso_now', lambda **kw: '2099-01-01T00:00:00Z')
    def report(cmd, **kw):
        assert Path(kw['cwd']) == Path.cwd()
        return SimpleNamespace(returncode=0, stdout=json.dumps({'result': {**SUCCESS['result'], 'id': promoted}}), stderr='')
    monkeypatch.setattr(tool_launch, 'run', report)
    assert post_deploy_polling.poll_once()['completed'] == 1


def test_capture_private_writer_and_portable_replay(tmp_path, monkeypatch):
    writes = []
    original = c.bundle.atomic_write_bytes
    def write(path, data):
        writes.append(path.name)
        return original(path, data)
    monkeypatch.setattr(c.bundle, 'atomic_write_bytes', write)
    assert _bulk_csv.write_capture(tmp_path / 'before.csv', ['Id', 'Description'], [{'Id': '001000000000001AAA', 'Description': None}])
    assert set(writes) == {'before.json', 'before.csv', 'replay.csv'}
    assert b'\r\n' not in (tmp_path / 'replay.csv').read_bytes()
    for op in ('data_bulk_update', 'data_bulk_upsert'):
        caps = revert_capabilities.compute_revertibility(op, {})
        assert '--line-ending LF' in caps['manual_recovery_path']
        warnings = ' '.join(caps['side_effects_warning']).lower()
        for term in ('automation', 'children', 'outside', 'later edits'):
            assert term in warnings


@pytest.mark.parametrize('kind', ['update', 'upsert'])
def test_relationship_capture_and_mixed_insert_load(context, tmp_path, monkeypatch, kind):
    path = tmp_path / 'input.csv'
    path.write_text('\ufeffiD,description,Account.Ext__c\n003000000000001,changed,A1\n' + (',new,A2\n' if kind == 'upsert' else ''), encoding='utf-8')
    calls = []
    def run(cmd, **kw):
        calls.append(cmd)
        if 'query' in cmd:
            assert 'Account.Id' in cmd[cmd.index('--query') + 1]
            return response({'result': {'records': [{'Id': '003000000000001AAA', 'Description': None, 'Account': {'Id': '001000000000001AAA', 'Ext__c': 'A0'}}]}})
        return response({'result': {'id': '750000000000001AAA', 'state': 'InProgress'}})
    monkeypatch.setattr(c, 'run_sf_subprocess', run)
    flags = ['--external-id', 'Id'] if kind == 'upsert' else []
    args = build_parser().parse_args(['data', 'bulk', kind, '-o', 'synthetic-org', '--sobject', 'Contact', '--file', str(path)] + flags)
    assert (upsert if kind == 'upsert' else update).run(args) == c.EXIT_IN_PROGRESS
    saved = manifest(context)['payload']
    rows = list(csv.DictReader(Path(saved['replay_csv']).open(encoding='utf-8')))
    assert rows[0]['Account.Ext__c'] == 'A0' and rows[0]['Description'] == '#N/A'
    assert rows[0]['Id'] == '003000000000001AAA'
    if kind == 'upsert':
        assert saved['input_row_count'] == 2 and saved['blank_key_insert_count'] == 1
    assert any('bulk' in cmd for cmd in calls)


def test_null_relationship_and_missing_column(tmp_path, capsys):
    assert _bulk_csv.write_capture(tmp_path / 'before.csv', ['Account.Ext__c'], [{'Id': '003000000000001AAA', 'Account': None}])
    data = json.loads((tmp_path / 'before.json').read_text(encoding='utf-8'))
    assert data['records'][0]['Account.Ext__c'] is None
    warnings = ' '.join(revert_capabilities.compute_revertibility('data_bulk_update', {})['side_effects_warning'])
    assert 'lookup' in warnings and 'external ID' in warnings
    assert not _bulk_csv.write_capture(tmp_path / 'missing.csv', ['Missing__c'], [{'Id': '003000000000001AAA'}])
    assert 'Missing__c' in capsys.readouterr().err


@pytest.mark.parametrize('production', [True, False])
def test_bad_update_id_names_row_before_query(context, tmp_path, monkeypatch, capsys, production):
    if not production:
        info = org_detect.resolve_org('synthetic-org')._replace(detected_org_type='developer')
        monkeypatch.setattr(org_detect, 'resolve_org', lambda *a: info)
    path = tmp_path / 'bad.csv'
    path.write_text('Id,Name\n001000000000001,Valid\nnot-an-id,Bad\n', encoding='utf-8')
    monkeypatch.setattr(c, 'run_sf_subprocess', lambda *a, **kw: pytest.fail('invalid row must not query or submit'))
    args = build_parser().parse_args(['data', 'bulk', 'update', '-o', 'synthetic-org', '--sobject', 'Account', '--file', str(path)])
    assert update.run(args) == (c.EXIT_PRESNAP_FAILED_PROD if production else c.EXIT_PRESNAP_FAILED_SANDBOX)
    assert 'row 3' in capsys.readouterr().err


@pytest.mark.parametrize('kind,wrapper,key,prefix', [('permset', org_assign_permset, 'psa', '0Pa'), ('permsetlicense', org_assign_permsetlicense, 'psl', '2LA')])
def test_assignment_names_capture_only_new_ids(context, monkeypatch, kind, wrapper, key, prefix):
    queries = []
    old, new = prefix + '000000000001AAA', prefix + '000000000002AAA'
    def run(cmd, **kw):
        if 'query' in cmd:
            queries.append(cmd[cmd.index('--query') + 1])
            ids = [old] if len(queries) == 1 else [old, new]
            return response({'result': {'done': True, 'totalSize': len(ids), 'records': [{'Id': i} for i in ids]}})
        return response({'result': {'successes': [{'name': 'user@example.invalid', 'value': 'Synthetic_A'}], 'failures': []}})
    monkeypatch.setattr(c, 'run_sf_subprocess', run)
    args = build_parser().parse_args(['org', 'assign', kind, '-o', 'synthetic-org', '--name', 'Synthetic_A', '--on-behalf-of', 'user@example.invalid'])
    assert wrapper.run(args) == 0
    saved = manifest(context)['payload']
    assert saved[f'created_{key}_ids'] == [new]
    assert len(queries) == 2 and all('user@example.invalid' in q and 'Synthetic_A' in q for q in queries)


@pytest.mark.parametrize('body', [{'result': {'id': JOB, 'status': 'Failed', 'done': True, 'success': False, 'details': {'componentFailures': [{'problem': 'Synthetic compile failure'}], 'runTestResult': {'failures': [{'message': 'Synthetic test failure'}]}}}}, {'message': 'Synthetic invalid flag'}])
def test_deploy_prints_reason(context, monkeypatch, capsys, body):
    monkeypatch.setattr(c, 'run_sf_subprocess', lambda *a, **k: response(body, 1))
    assert deploy.run(build_parser().parse_args(['deploy', '-o', 'synthetic-org'])) != 0
    err = capsys.readouterr().err
    assert 'Synthetic' in err
    assert 'may still be running' not in err


def test_apex_error_data_and_nonproduction_steering(context, tmp_path, monkeypatch, capsys):
    info = org_detect.resolve_org('synthetic-org')._replace(detected_org_type='developer')
    monkeypatch.setattr(org_detect, 'resolve_org', lambda *a: info)
    path = tmp_path / 'run.apex'
    path.write_text('throw new IllegalArgumentException();', encoding='utf-8')
    monkeypatch.setattr(c, 'run_sf_subprocess', lambda *a, **k: response({'message': 'Execution failed', 'data': {'exceptionMessage': 'Synthetic exception', 'logs': 'Synthetic log', 'success': False}}, 1))
    assert apex_run.run(build_parser().parse_args(['apex', 'run', '-o', 'synthetic-org', '-f', str(path)])) == c.EXIT_UNDERLYING_FAILED
    err = capsys.readouterr().err
    assert 'Synthetic exception' in err and 'torque data' in err and 'NOT automatically revertible' in err
    assert manifest(context)['payload']['exception_message'] == 'Synthetic exception'


def test_dry_run_value_does_not_skip_approval(context, monkeypatch):
    seen = []
    monkeypatch.setattr(c, 'connected_approval', lambda *a, **kw: (seen.append(kw['dry_run']) or c.EXIT_NOT_APPROVED, None))
    args = build_parser().parse_args(['deploy', '-o', 'synthetic-org', '--tests=--dry-run'])
    assert deploy.run(args) == c.EXIT_NOT_APPROVED
    assert seen == [False]


def test_relevant_tests_and_working_retrieve_advice():
    assert build_parser().parse_args(['deploy', '-o', 'synthetic-org', '--test-level', 'RunRelevantTests']).test_level == 'RunRelevantTests'
    advice = revert_capabilities.compute_revertibility('deploy_quick_promote', {})['manual_recovery_path']
    assert '--target-metadata-dir' in advice and '--output-dir' not in advice


def test_subprocess_timeout_without_id_warns(context, monkeypatch, capsys):
    monkeypatch.setattr(c, 'run_sf_subprocess', lambda *a, **kw: (-1, '', 'TIMEOUT after 1800s'))
    assert deploy.run(build_parser().parse_args(['deploy', '-o', 'synthetic-org'])) == c.EXIT_POST_FINALIZE_FAILED
    assert 'may still be running' in capsys.readouterr().err
    assert manifest(context)['payload']['recovery_readiness'] == 'unknown'


def test_missing_cli_does_not_claim_running_deploy(context, monkeypatch, capsys):
    monkeypatch.setattr(c, 'run_sf_subprocess', lambda *a, **kw: (-2, '', 'sf CLI not found in PATH'))
    assert deploy.run(build_parser().parse_args(['deploy', '-o', 'synthetic-org'])) != 0
    err = capsys.readouterr().err
    assert 'sf CLI not found' in err and 'may still be running' not in err


def test_soql_callers_share_control_character_encoding():
    from jsc_common.soql import soql_literal
    from jsc_advisory import sf
    from jsc_revert.wrappers import data_upsert
    assert sf.soql_literal is soql_literal
    assert data_upsert.soql_literal is soql_literal
    assert soql_literal("a\nb\tc\\d'e") == "a\\nb\\tc\\\\d\\'e"


def test_nonproduction_short_capture_warns(context, tmp_path, monkeypatch, capsys):
    info = org_detect.resolve_org('synthetic-org')._replace(detected_org_type='developer')
    monkeypatch.setattr(org_detect, 'resolve_org', lambda *a: info)
    path = tmp_path / 'input.csv'
    path.write_text('Id,Name\n001000000000001,Synthetic\n', encoding='utf-8')
    def run(cmd, **kw):
        if 'query' in cmd:
            return response({'message': 'Synthetic read failure'}, 1)
        return response({'result': {'id': '750000000000001AAA', 'state': 'InProgress'}})
    monkeypatch.setattr(c, 'run_sf_subprocess', run)
    args = build_parser().parse_args(['data', 'bulk', 'update', '-o', 'synthetic-org', '--sobject', 'Account', '--file', str(path)])
    assert update.run(args) == c.EXIT_IN_PROGRESS
    assert 'before-state capture short (0/1 rows)' in capsys.readouterr().err


@pytest.mark.parametrize('kind,wrapper,key', [('permset', org_assign_permset, 'psa'), ('permsetlicense', org_assign_permsetlicense, 'psl')])
@pytest.mark.parametrize('query_result', [None, {'done': False, 'totalSize': 2, 'records': []}, {'done': True, 'totalSize': 2, 'records': []}])
def test_assignment_incomplete_query_never_invents_new_ids(context, monkeypatch, kind, wrapper, key, query_result):
    def run(cmd, **kw):
        if 'query' in cmd:
            return response({'result': query_result})
        return response({'result': {'successes': [{'name': 'user@example.invalid', 'value': 'Synthetic_A'}], 'failures': []}})
    monkeypatch.setattr(c, 'run_sf_subprocess', run)
    args = build_parser().parse_args(['org', 'assign', kind, '-o', 'synthetic-org', '--name', 'Synthetic_A', '--on-behalf-of', 'user@example.invalid'])
    assert wrapper.run(args) == 0
    saved = manifest(context)['payload']
    assert saved[f'created_{key}_ids'] == []
    assert saved[f'{key}_id_capture_status'] == 'not_returned'


def test_assignment_duplicate_does_not_become_recovery_id(context, monkeypatch):
    def run(cmd, **kw):
        if 'query' in cmd:
            return response({'result': {'done': True, 'totalSize': 1, 'records': [{'Id': '2LA000000000001AAA'}]}})
        return response({'result': {'successes': [{'name': 'user@example.invalid', 'value': 'Synthetic_A'}], 'failures': []}})
    monkeypatch.setattr(c, 'run_sf_subprocess', run)
    args = build_parser().parse_args(['org', 'assign', 'permsetlicense', '-o', 'synthetic-org', '--name', 'Synthetic_A', '--on-behalf-of', 'user@example.invalid'])
    assert org_assign_permsetlicense.run(args) == 0
    assert manifest(context)['payload']['created_psl_ids'] == []


@pytest.mark.parametrize('async_mode', [False, True])
def test_staged_project_remains_available_for_exact_job_report(context, tmp_path, monkeypatch, async_mode):
    source = tmp_path / 'source' / 'flows'
    source.mkdir(parents=True)
    (source / 'Synthetic.flow-meta.xml').write_text('<Flow/>', encoding='utf-8')
    monkeypatch.setattr(c, 'in_sfdx_project', lambda: False)
    started = []
    def run(cmd, **kw):
        if 'start' in cmd:
            started.append(Path(kw['cwd']))
            assert (started[0] / 'sfdx-project.json').exists()
            return response(QUEUED)
        assert kw.get('cwd') == str(started[0])
        assert (started[0] / 'sfdx-project.json').exists()
        return response(SUCCESS)
    monkeypatch.setattr(c, 'run_sf_subprocess', run)
    flags = ['--async'] if async_mode else []
    args = build_parser().parse_args(['deploy', '-o', 'synthetic-org', '--source-dir', str(source.parent)] + flags)
    assert deploy.run(args) == (c.EXIT_IN_PROGRESS if async_mode else 0)
    if async_mode:
        assert started[0].is_dir()
        assert manifest(context)['payload']['report_cwd'] == str(started[0])
        monkeypatch.setattr(post_deploy_polling, '_iso_now', lambda **kw: '2099-01-01T00:00:00Z')
        def report(cmd, **kw):
            assert Path(kw['cwd']) == started[0]
            assert (started[0] / 'sfdx-project.json').exists()
            return SimpleNamespace(returncode=0, stdout=json.dumps(SUCCESS), stderr='')
        monkeypatch.setattr(tool_launch, 'run', report)
        assert post_deploy_polling.poll_once()['completed'] == 1
