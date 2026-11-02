"""Recovery-round regressions, using synthetic CLI responses only."""
import csv
import json
from uuid import uuid4

import pytest

from jsc_revert import org_detect, post_deploy_polling, revert_capabilities
from jsc_revert.cli import build_parser
from jsc_revert.wrappers import _common as c, apex_run, data_bulk_update as update
from jsc_revert.wrappers import data_bulk_upsert as upsert, deploy, org_assign_permset, org_assign_permsetlicense


@pytest.fixture
def context(tmp_path, monkeypatch):
    # Keep the real Windows private-file API below MAX_PATH in the offline runner.
    root = tmp_path.parent / ('r-' + uuid4().hex[:6])
    monkeypatch.setenv('TORQUE_WORKSPACE', str(root))
    monkeypatch.delenv('JSC_REVERT_NONREVERTIBLE_AUTO_ACK', raising=False)
    org = org_detect.OrgInfo('synthetic-org', '00D000000000001AAA', '00D000000000001', False,
                             'https://example.invalid', 'https://login.salesforce.com', 'production')
    monkeypatch.setattr(org_detect, 'resolve_org', lambda *a, **k: org)
    monkeypatch.setattr(c.mf, 'get_sf_cli_version', lambda: 'synthetic')
    return root


def manifest(root):
    return json.loads(next(root.rglob('manifest.json')).read_text(encoding='utf-8'))


def test_bom_and_case_identifiers(tmp_path):
    path = tmp_path / 'input.csv'
    path.write_text('\ufeffiD,description\n001000000000001AAA,new\n', encoding='utf-8')
    assert update._parse_bulk_csv(path)[0] == ['001000000000001AAA']
    path.write_text('\ufeffext__c,description\nE1,new\n', encoding='utf-8')
    assert upsert._parse_bulk_csv_ext_id(path, 'Ext__c')[0] == ['E1']


@pytest.mark.parametrize('kind', ['update', 'upsert'])
@pytest.mark.parametrize('body', ['Wrong,Description\nx,new\n', 'Id,Ext__c,Description\n,,new\n'])
def test_missing_identifiers_never_submit_production(context, tmp_path, monkeypatch, kind, body):
    path = tmp_path / 'input.csv'
    path.write_text(body, encoding='utf-8')
    monkeypatch.setattr(c, 'run_sf_subprocess', lambda *a, **k: pytest.fail('must not submit'))
    extra = ['--external-id', 'Ext__c'] if kind == 'upsert' else []
    args = build_parser().parse_args(['data', 'bulk', kind, '-o', 'synthetic-org', '--sobject', 'Account', '--file', str(path)] + extra)
    assert (upsert if kind == 'upsert' else update).run(args) == c.EXIT_PRESNAP_FAILED_PROD


@pytest.mark.parametrize('kind', ['update', 'upsert'])
def test_capture_has_canonical_id_typed_state_and_null_replay(tmp_path, monkeypatch, kind):
    calls = []
    record = {'Id': '001000000000001AAA', 'Ext__c': 'E1', 'Description': None}
    def run(cmd, **kwargs):
        calls.append(cmd[cmd.index('--query') + 1])
        return 0, json.dumps({'result': {'records': [record]}}), ''
    monkeypatch.setattr(c, 'run_sf_subprocess', run)
    path = tmp_path / 'before.csv'
    if kind == 'update':
        assert update._bulk_query_before('synthetic-org', 'Account', [record['Id']], ['id', 'description'], path) == 1
    else:
        assert upsert._query_by_ext_ids_tagged('synthetic-org', 'Account', 'Ext__c', ['E1'], ['ext__c', 'description'], path) == ('ok', 1)
    assert 'Id' in calls[0].split(' FROM ')[0]
    rows = list(csv.DictReader(path.open(encoding='utf-8')))
    assert rows[0]['Id'] == record['Id']
    assert json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))['records'][0]['Description'] is None
    replay = list(csv.DictReader((tmp_path / 'replay.csv').open(encoding='utf-8')))
    assert replay[0]['Description'] == '#N/A'


def test_external_id_soql_escapes(tmp_path, monkeypatch):
    queries = []
    def run(cmd, **kwargs):
        queries.append(cmd[cmd.index('--query') + 1])
        return 0, '{"result":{"records":[]}}', ''
    monkeypatch.setattr(c, 'run_sf_subprocess', run)
    upsert._query_by_ext_ids_tagged('synthetic-org', 'Account', 'Ext__c', ["part'1", 'a\\b', 'a\nb'], ['Ext__c'], tmp_path / 'before.csv')
    assert "IN ('part\\'1','a\\\\b','a\\nb')" in queries[0]


@pytest.mark.parametrize('kind,wrapper', [('permset', org_assign_permset), ('permsetlicense', org_assign_permsetlicense)])
def test_repeated_assignment_values_survive(context, monkeypatch, kind, wrapper):
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        return 0, '{"result":{"successes":[{"name":"user-a@example.invalid","value":"Synthetic_A"}],"failures":[]}}', ''
    monkeypatch.setattr(c, 'run_sf_subprocess', run)
    args = build_parser().parse_args(['org', 'assign', kind, '-o', 'synthetic-org', '--name', 'Synthetic_A', '--name', 'Synthetic_B', '--on-behalf-of', 'user-a@example.invalid', '--on-behalf-of', 'user-b@example.invalid'])
    assert wrapper.run(args) == 0
    command = next(cmd for cmd in calls if 'assign' in cmd)
    assert command.count('--name') == 2
    assert command.count('--on-behalf-of') == 2
    saved = manifest(context)
    assert 'Synthetic_A' in saved['wrapper_command'] and 'Synthetic_B' in saved['wrapper_command']
    assert saved['payload']['on_behalf_of'] == ['user-a@example.invalid', 'user-b@example.invalid']


def test_apex_production_does_not_read_stdin(context, tmp_path, monkeypatch):
    path = tmp_path / 'run.apex'
    path.write_text('System.debug(1);', encoding='utf-8')
    monkeypatch.setattr('builtins.input', lambda: pytest.fail('agent has no interactive stdin'))
    monkeypatch.setattr(c, 'run_sf_subprocess', lambda *a, **k: (0, '{"result":{"success":true}}', ''))
    args = build_parser().parse_args(['apex', 'run', '-o', 'synthetic-org', '-f', str(path)])
    assert apex_run.run(args) == 0


@pytest.mark.parametrize('flags', [[], ['--test-level', 'RunSpecifiedTests', '--tests', 'Synthetic_A', '--tests', 'Synthetic_B', '--wait', '5'], ['--async'], ['--post-destructive-changes', 'post.xml']])
def test_deploy_wait_and_options_record_pending_job(context, monkeypatch, flags):
    calls, queued = [], []
    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        # CLI 2.150.6 / deploy plugin 4.1.2 submission and wait-expiry shapes.
        if 'start' in cmd:
            return 0, '{"result":{"id":"0Af000000000001AAA","status":"Queued","done":false,"files":[]}}', ''
        return 0, '{"result":{"id":"0Af000000000001AAA","status":"InProgress","done":false,"success":false}}', ''
    monkeypatch.setattr(c, 'run_sf_subprocess', run)
    monkeypatch.setattr(c, 'auto_enqueue_if_pending', lambda *a: queued.append(a))
    args = build_parser().parse_args(['deploy', '-o', 'synthetic-org'] + flags)
    assert deploy.run(args) == c.EXIT_IN_PROGRESS
    cmd, kwargs = calls[0]
    assert '--async' in cmd and '--wait' not in cmd
    if '--async' not in flags:
        report, kw = calls[1]
        assert int(report[report.index('--wait') + 1]) * 60 < kw['timeout_seconds']
    for flag in flags:
        assert any(flag in command for command, kw in calls)
    saved = manifest(context)
    assert saved['payload']['deploy_id'] == '0Af000000000001AAA' and queued
    assert saved['payload']['recovery_readiness'] == 'unknown'
    for flag in flags:
        assert flag in saved['wrapper_command']
        assert flag in saved['payload']['selectors']['deploy_options']
    if '--post-destructive-changes' in flags:
        assert saved['payload']['selectors']['destructive_manifest_paths'] == ['post.xml']
        assert saved['revert_capabilities']['automatic_revertible'] is False


def test_async_completion_records_unknown_recovery(tmp_path, monkeypatch):
    saved = {'operation_type': 'deploy_metadata', 'snapshot_status': 'pending_finalize_required', 'payload': {'deploy_id': '0Af000000000001AAA'}, 'revert_capabilities': {'automatic_revertible': True}}
    monkeypatch.setattr(post_deploy_polling.mf, 'load_by_id', lambda *a: (tmp_path, saved))
    monkeypatch.setattr(post_deploy_polling.mf, 'save', lambda *a: None)
    entry = dict(org_id_short='00D000000000001', alias='synthetic-org', snapshot_id='synthetic', job_id='0Af000000000001AAA', queue_entry_id='synthetic', retry_count=1)
    post_deploy_polling._update_snapshot_manifest(entry, 'complete')
    assert saved['snapshot_status'] == 'complete'
    assert saved['payload']['recovery_readiness'] == 'unknown'
    assert saved['revert_capabilities']['automatic_revertible'] is False
    assert 'No before-state was captured' in saved['revert_capabilities']['manual_recovery_path']


def test_quick_deploy_advice_names_real_retrieve():
    advice = revert_capabilities.compute_revertibility('deploy_quick_promote', {})['manual_recovery_path']
    assert 'jsc deploy --dry-run' not in advice
    assert 'sf project retrieve start' in advice
    assert '--target-metadata-dir' in advice and '--output-dir' not in advice


def test_staged_deploy_preserves_relative_destructive_manifest(context, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'post.xml').write_text('<Package/>', encoding='utf-8')
    stage = tmp_path / 'stage'
    stage.mkdir()
    monkeypatch.setattr(c, 'in_sfdx_project', lambda: False)
    monkeypatch.setattr(c, 'stage_source_project', lambda *a: (str(stage), ['force-app']))
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        return 1, '{"result":{"id":"0Af000000000001AAA","status":"Failed","done":true,"success":false}}', ''
    monkeypatch.setattr(c, 'run_sf_subprocess', run)
    args = build_parser().parse_args(['deploy', '-o', 'synthetic-org', '--source-dir', 'source', '--post-destructive-changes', 'post.xml'])
    assert deploy.run(args) == c.EXIT_UNDERLYING_FAILED
    assert calls[0][calls[0].index('--post-destructive-changes') + 1] == str(tmp_path / 'post.xml')
    assert manifest(context)['payload']['selectors']['destructive_manifest_paths'] == ['post.xml']
