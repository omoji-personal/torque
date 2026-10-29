"""Regressions for the Round 2 review; every Salesforce call is synthetic."""
import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jsc_revert import deploy_stage, org_detect, post_deploy_polling, revert_capabilities, revert_executor, revert_planner, stale_detector, tool_launch
from jsc_revert.cli import build_parser
from jsc_revert.wrappers import _bulk_csv, _common as c, deploy, org_assign_permset, org_assign_permsetlicense
from packages.revert.tests.test_recovery_round2 import context, before, manifest, response, QUEUED, SUCCESS, JOB


def entries(root):
    paths = list(root.rglob('queue.jsonl'))
    return post_deploy_polling._load_entries(paths[0]) if paths else []


def test_interrupted_wait_is_already_queued(context, monkeypatch):
    def run(cmd, **kw):
        if 'start' in cmd:
            return response(QUEUED)
        raise KeyboardInterrupt
    monkeypatch.setattr(c, 'run_sf_subprocess', run)
    monkeypatch.setattr(c, 'in_sfdx_project', lambda: True)
    with pytest.raises(KeyboardInterrupt):
        deploy.run(build_parser().parse_args(['deploy', '-o', 'synthetic-org', '--source-dir', 'force-app']))
    assert manifest(context)['payload']['deploy_id'] == JOB
    assert [e['job_id'] for e in entries(context)] == [JOB]
    monkeypatch.setattr(post_deploy_polling, '_iso_now', lambda **kw: '2099-01-01T00:00:00Z')
    monkeypatch.setattr(tool_launch, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps(SUCCESS), stderr=''))
    assert post_deploy_polling.poll_once()['completed'] == 1


def test_revert_child_timeout_covers_submission_and_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(revert_planner, 'recovery_files', lambda *a: [tmp_path / 'A.flow-meta.xml'])
    cmd = revert_planner.build_revert_command({'operation_type': 'deploy_metadata', 'payload': {},
        'org': {'alias': 'synthetic-org'}, 'snapshot_id': 's1'}, tmp_path)
    args = build_parser().parse_args(cmd[len(revert_planner._jsc_command()):])
    monkeypatch.setenv('JSC_REVERT_EXECUTE_TIMEOUT_S', '1800')
    assert revert_executor.execution_timeout(cmd) > 1800 + args.wait * 60 + 600


@pytest.mark.parametrize('parent', [None, {'Id': '001000000000001AAA', 'Ext__c': None}])
def test_relationship_parent_id_distinguishes_missing_key(tmp_path, parent):
    fields = ['Id', 'Account.Ext__c', 'Parent__r.Ext__c']
    selected = _bulk_csv.capture_fields(fields)
    assert 'Account.Id' in selected and 'Parent__r.Id' in selected
    records = [{'Id': '003000000000001AAA', 'Account': parent, 'Parent__r': parent}]
    assert _bulk_csv.write_capture(tmp_path / 'before.csv', fields, records)
    data = json.loads((tmp_path / 'before.json').read_text(encoding='utf-8'))
    for name in ('Account', 'Parent__r'):
        assert data['records'][0][name + '.Id'] == (parent['Id'] if parent else None)
    with (tmp_path / 'replay.csv').open(encoding='utf-8', newline='') as stream:
        replay = csv.DictReader(stream)
        assert list(replay.fieldnames) == fields  # evidence columns are not update columns
        assert next(replay)['Account.Ext__c'] == ''
    warnings = ' '.join(revert_capabilities.compute_revertibility('data_bulk_update', {})['side_effects_warning'])
    assert 'before.json' in warnings and 'Relationship.Id' in warnings


@pytest.mark.parametrize('async_mode', [False, True])
def test_short_stage_retained_until_terminal(context, tmp_path, monkeypatch, async_mode):
    source = tmp_path.parent / 'src3'
    relative = Path('main/default/staticresources') / ('Synthetic_' + 'a' * 30) / ('b' * 35 + '.js')
    (source / relative).parent.mkdir(parents=True, exist_ok=True)
    (source / relative).write_text('synthetic', encoding='utf-8')
    monkeypatch.setattr(c, 'in_sfdx_project', lambda: False)
    stages = []
    def run(cmd, **kw):
        stage = Path(kw['cwd'])
        if 'start' in cmd:
            stages.append(stage)
            assert stage.parent == Path(__import__('tempfile').gettempdir()).resolve()
            assert len(str(stage / 'force-app' / relative)) < 260
            assert (stage / 'force-app' / relative).read_text(encoding='utf-8') == 'synthetic'
            return response(QUEUED)
        assert stage == stages[0] and stage.is_dir()
        return response(SUCCESS)
    monkeypatch.setattr(c, 'run_sf_subprocess', run)
    flags = ['--async'] if async_mode else []
    assert deploy.run(build_parser().parse_args(['deploy', '-o', 'synthetic-org', '--source-dir', str(source)] + flags)) == (22 if async_mode else 0)
    if async_mode:
        assert stages[0].is_dir()
        monkeypatch.setattr(post_deploy_polling, '_iso_now', lambda **kw: '2099-01-01T00:00:00Z')
        def report(cmd, **kw):
            assert Path(kw['cwd']) == stages[0] and stages[0].is_dir()
            return SimpleNamespace(returncode=0, stdout=json.dumps(SUCCESS), stderr='')
        monkeypatch.setattr(tool_launch, 'run', report)
        assert post_deploy_polling.poll_once()['completed'] == 1
    assert not stages[0].exists()
    # The early queue entry must not report from a stage removed by the wrapper.
    monkeypatch.setattr(tool_launch, 'run', lambda *a, **kw: pytest.fail('terminal snapshot needs no report'))
    monkeypatch.setattr(post_deploy_polling, '_iso_now', lambda **kw: '2099-01-01T00:00:00Z')
    post_deploy_polling.poll_once()


@pytest.mark.parametrize('kind,wrapper,key', [('permset', org_assign_permset, 'psa'), ('permsetlicense', org_assign_permsetlicense, 'psl')])
@pytest.mark.parametrize('alias', [False, True])
def test_assignment_capture_uses_resolved_identity_only(context, monkeypatch, kind, wrapper, key, alias):
    info = org_detect.resolve_org('synthetic-org')
    # Works before OrgInfo gains username, so the regression reaches the unsafe calls.
    info = SimpleNamespace(**{**info._asdict(), 'username': 'user@example.invalid'}, is_production=True)
    monkeypatch.setattr(org_detect, 'resolve_org', lambda *a: info)
    commands = []
    def run(cmd, **kw):
        commands.append(cmd)
        assert cmd[:3] not in (['sf', 'org', 'display'], ['sf', 'alias', 'list'])
        if 'query' in cmd:
            assert 'user@example.invalid' in cmd[cmd.index('--query') + 1]
            return response({'result': {'done': True, 'totalSize': 0, 'records': []}})
        return response({'result': {'successes': [{'name': 'user@example.invalid', 'value': 'Synthetic_A'}], 'failures': []}})
    monkeypatch.setattr(c, 'run_sf_subprocess', run)
    flags = ['--on-behalf-of', 'synthetic-user'] if alias else []
    assert wrapper.run(build_parser().parse_args(['org', 'assign', kind, '-o', 'synthetic-org', '--name', 'Synthetic_A'] + flags)) == 0
    assert sum('query' in cmd for cmd in commands) == (0 if alias else 2)
    assert manifest(context)['payload'][key + '_id_capture_status'] == ('not_returned' if alias else 'captured')


@pytest.mark.parametrize('selector', [['--source-dir', 'force-app'], ['--manifest', 'package.xml']])
def test_missing_before_state_keeps_selector_reason(context, monkeypatch, selector):
    monkeypatch.setattr(c, 'in_sfdx_project', lambda: True)
    monkeypatch.setattr(c, 'run_sf_subprocess', lambda cmd, **kw: response(QUEUED if 'start' in cmd else SUCCESS))
    assert deploy.run(build_parser().parse_args(['deploy', '-o', 'synthetic-org'] + selector)) == 0
    advice = revert_capabilities.effective_capabilities(manifest(context))['manual_recovery_path']
    assert 'express it with --metadata' in advice


def test_before_write_label_is_conditional(context, monkeypatch):
    before(monkeypatch)
    def run(cmd, **kw):
        if 'start' in cmd:
            caps = manifest(context)['revert_capabilities']
            assert caps['automatic_revertible'] is False
            assert 'restorable if the deploy completes and post-deploy capture succeeds' in caps['manual_recovery_path']
        return response(QUEUED)
    monkeypatch.setattr(c, 'run_sf_subprocess', run)
    assert deploy.run(build_parser().parse_args(['deploy', '-o', 'synthetic-org', '--metadata', 'Flow:Synthetic', '--async'])) == 22


def test_quick_baseline_advice_unzips():
    assert '--unzip' in revert_capabilities.compute_revertibility('deploy_quick_promote', {})['manual_recovery_path']


def test_poll_defers_while_wrapper_captures(context, monkeypatch):
    before(monkeypatch)
    monkeypatch.setattr(post_deploy_polling, '_iso_now', lambda **kw: '2099-01-01T00:00:00Z')
    monkeypatch.setattr(tool_launch, 'run', lambda *a, **kw: pytest.fail('live lease must defer polling'))
    def run(cmd, **kw):
        if 'start' in cmd:
            return response(QUEUED)
        assert len(entries(context)) == 1
        assert post_deploy_polling.poll_once()['completed'] == 0
        return response(SUCCESS)
    monkeypatch.setattr(c, 'run_sf_subprocess', run)
    monkeypatch.setattr(stale_detector, 'capture_deployment_state', lambda d, m, t: m['payload'].__setitem__('after_state', {'captured': True}))
    assert deploy.run(build_parser().parse_args(['deploy', '-o', 'synthetic-org', '--metadata', 'Flow:Synthetic'])) == 0
    assert manifest(context)['revert_capabilities']['automatic_revertible'] is True
    monkeypatch.setattr(post_deploy_polling, '_iso_now', lambda **kw: '2099-02-01T00:00:00Z')
    assert post_deploy_polling.poll_once()['completed'] == 1
    assert manifest(context)['revert_capabilities']['automatic_revertible'] is True


def test_cleanup_never_deletes_caller_project(tmp_path):
    (tmp_path / 'sfdx-project.json').write_text('{}', encoding='utf-8')
    deploy_stage.cleanup({'snapshot_id': 's1', 'snapshot_status': 'complete',
                          'payload': {'report_cwd': str(tmp_path), 'staged_project': str(tmp_path)}})
    assert (tmp_path / 'sfdx-project.json').exists()


def test_resolved_username_excludes_auth_material(monkeypatch):
    from packages.revert.tests.test_authoritative_org_identity import fake_commands, authority, envelope, ID18
    fake_commands(monkeypatch, authority(), display=envelope({'id': ID18,
        'username': 'user@example.invalid', 'accessToken': 'SYNTHETIC_AUTH_VALUE'}))
    info = org_detect.resolve_org('client-test', 3)
    assert info.username == 'user@example.invalid'
    assert 'SYNTHETIC_AUTH_VALUE' not in repr(info)
