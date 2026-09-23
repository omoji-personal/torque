"""Synthetic bulk capture and owned-lease regressions; no external tools."""
import csv
import json
import os
import re
import socket
from types import SimpleNamespace

import pytest

from jsc_revert import bundle, org_detect, org_sequence
from jsc_revert.cli import build_parser
from jsc_revert.wrappers import _common as common, data_bulk_delete as delete


def org():
    return org_detect.OrgInfo('synthetic-org', '00D000000000001AAA', '00D000000000001', True,
                              'https://example.invalid', 'https://test.salesforce.com', 'sandbox')


def fixture_records():
    return [{'Id': f'00100000000000{i}AAA', 'Name': f'Synthetic {i}',
             'Important__c': f'private custom {i}', 'Nullable__c': None,
             'Enabled__c': bool(i % 2), 'Compound__c': {'city': 'Synthetic'}} for i in range(1, 4)]


def query_backend(records, calls, *, omit=None):
    fields = list(records[0])
    def run(command, **kwargs):
        calls.append(command)
        if command[1:3] == ['sobject', 'describe']:
            return 0, json.dumps({'result': {'fields': [{'name': name, 'type': 'string'} for name in fields]}}), ''
        query = command[command.index('--query') + 1]
        selected = query.split(' FROM ')[0].removeprefix('SELECT ').split(',')
        ids = set(re.findall(r"'([A-Za-z0-9]+)'", query))
        rows = [{field: row[field] for field in selected if field != omit} for row in records if row['Id'] in ids]
        return 0, json.dumps({'result': {'records': rows, 'done': True}}), ''
    return run


def test_custom_fields_and_exact_types_survive_bounded_capture(tmp_path, monkeypatch):
    records, calls, capture = fixture_records(), [], {}
    monkeypatch.setattr(delete, 'PAGE_SIZE', 2)
    monkeypatch.setattr(delete, 'FIELD_PAGE_SIZE', 2)
    monkeypatch.setattr(common, 'run_sf_subprocess', query_backend(records, calls))
    path = tmp_path / 'before.csv'
    assert delete._query_all_fields('synthetic-org', 'Account', [r['Id'] for r in records], path, capture=capture) == 3
    assert capture['complete'] is True
    assert set(capture['fields_captured']) == set(records[0])
    assert json.loads(path.with_suffix('.json').read_text(encoding="utf-8"))['records'] == records
    with path.open(encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[2]['Important__c'] == 'private custom 3'
    assert len(calls) == 7  # describe + two record chunks times three field chunks
    assert all('FIELDS(STANDARD)' not in ' '.join(call) for call in calls)
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('omit', ['Important__c', 'Id'])
def test_missing_requested_field_is_incomplete_not_a_null_value(tmp_path, monkeypatch, omit):
    capture = {}
    records = fixture_records()
    monkeypatch.setattr(common, 'run_sf_subprocess', query_backend(records, [], omit=omit))
    assert delete._query_all_fields('synthetic-org', 'Account', [r['Id'] for r in records], tmp_path/'before.csv', capture=capture) == 0
    assert capture['complete'] is False and capture['error']
    assert not (tmp_path/'before.csv').exists()


def test_missing_requested_record_is_incomplete(tmp_path, monkeypatch):
    records, capture = fixture_records(), {}
    monkeypatch.setattr(common, 'run_sf_subprocess', query_backend(records[:1], []))
    assert delete._query_all_fields('synthetic-org', 'Account', [r['Id'] for r in records], tmp_path/'before.csv', capture=capture) == 0
    assert capture['complete'] is False


def test_binary_reference_cannot_masquerade_as_saved_binary_content(tmp_path, monkeypatch):
    capture = {}
    monkeypatch.setattr(common, 'run_sf_subprocess', lambda *a, **k: (0, json.dumps({'result': {'fields': [{'name': 'Id'}, {'name': 'Body', 'type': 'base64'}]}}), ''))
    assert delete._query_all_fields('synthetic-org', 'Attachment', ['001000000000001AAA'], tmp_path/'before.csv', capture=capture) == 0
    assert 'Binary' in capture['error']


def test_default_bulk_delete_reaches_dml_with_a_maintained_lease(tmp_path, monkeypatch):
    records, calls = fixture_records(), []
    input_path = tmp_path/'input.csv'
    input_path.write_text('Id\n' + '\n'.join(r['Id'] for r in records) + '\n', encoding="utf-8")
    monkeypatch.setenv('TORQUE_WORKSPACE', str(tmp_path/'client'))
    monkeypatch.delenv('JSC_REVERT_LONG_OP_OK', raising=False)
    monkeypatch.setattr(org_detect, 'resolve_org', lambda *a, **k: org())
    monkeypatch.setattr(common.mf, 'get_sf_cli_version', lambda: 'synthetic')
    backend = query_backend(records, calls)
    def run(command, **kwargs):
        active = common._ACTIVE_WRAPPER.get()
        assert active._lease_thread.is_alive()
        if command[1:4] == ['data', 'delete', 'bulk']:
            calls.append(command)
            assert command[command.index('--wait') + 1] == '30'
            return SimpleNamespace(returncode=0, stdout=json.dumps({'result': {'jobInfo': {'id': '750000000000001AAA', 'state': 'JobComplete', 'numberRecordsFailed': 0, 'numberRecordsProcessed': 3}}}), stderr='')
        code, out, error = backend(command, **kwargs)
        return SimpleNamespace(returncode=code, stdout=out, stderr=error)
    monkeypatch.setattr(common.subprocess, 'run', run)
    args = build_parser().parse_args(['data','bulk','delete','-o','synthetic-org','--sobject','Account','--file',str(input_path)])
    assert delete.run(args) == 0
    manifest = json.loads(next((tmp_path/'client').rglob('manifest.json')).read_text(encoding="utf-8"))
    assert manifest['payload']['field_capture']['complete'] is True
    assert manifest['payload']['before_json']
    assert common._ACTIVE_WRAPPER.get() is None
    assert not list((tmp_path/'client').rglob('.org_lock.json'))
    assert sum(call[1:4] == ['data','delete','bulk'] for call in calls) == 1


@pytest.mark.parametrize('state,expected', [(org_sequence.PidStatus.WRAPPER, False), (org_sequence.PidStatus.UNKNOWN, False), (org_sequence.PidStatus.NOT_WRAPPER, True)])
def test_local_liveness_still_matters_after_the_old_hard_timeout(tmp_path, monkeypatch, state, expected):
    result = org_sequence.LockReadResult(org_sequence.LockReadStatus.VALID,
        {'heartbeat_at_iso': 'synthetic', 'owner_pid': 123, 'owner_hostname': socket.gethostname()})
    monkeypatch.setattr(org_sequence, '_seconds_since_iso', lambda _: 901)
    monkeypatch.setattr(org_sequence, '_check_pid_is_jsc_wrapper', lambda _: state)
    assert org_sequence._is_stale(tmp_path/'lock', result) is expected


def test_refresh_loop_uses_periodic_owned_heartbeats_without_sleep(monkeypatch):
    ctx = common.WrapperContext(operation_type='data_bulk_delete', target_org='synthetic-org', wrapper_command='synthetic')
    waits, heartbeats = [], []
    class Stop:
        def wait(self, seconds):
            waits.append(seconds)
            return len(waits) == 3
    ctx._lease_stop = Stop()
    monkeypatch.setattr(ctx, '_heartbeat_once', lambda: heartbeats.append('owned refresh'))
    ctx._refresh_lease()
    assert heartbeats == ['owned refresh', 'owned refresh']
    assert waits == [org_sequence.LOCK_HEARTBEAT_SECONDS] * 3


def test_owner_loss_does_not_execute_next_command_or_replace_new_owners_lock(tmp_path, monkeypatch):
    monkeypatch.setenv('TORQUE_WORKSPACE', str(tmp_path/'alpha'))
    ctx = common.WrapperContext(operation_type='data_bulk_delete', target_org='synthetic-org', wrapper_command='synthetic')
    ctx.org = org()
    assert ctx.acquire_org_lock() == 0
    state = json.loads(ctx._lock_path.read_text(encoding="utf-8"))
    state['owner_token'] = 'other-owner'
    bundle.atomic_write_json(ctx._lock_path, state)
    monkeypatch.setattr(common.subprocess, 'run', lambda *a, **k: pytest.fail('must not execute'))
    try:
        with pytest.raises(org_sequence.LockOwnershipError):
            common.run_sf_subprocess(['sf','data','delete','bulk'])
    finally:
        with pytest.raises(org_sequence.LockOwnershipError):
            ctx.release_lock()
    assert json.loads(ctx._lock_path.read_text(encoding="utf-8"))['owner_token'] == 'other-owner'
    assert common._ACTIVE_WRAPPER.get() is None


def test_heartbeat_and_release_remain_on_original_client_after_environment_change(tmp_path, monkeypatch):
    monkeypatch.setenv('TORQUE_WORKSPACE', str(tmp_path/'alpha'))
    ctx = common.WrapperContext(operation_type='data_bulk_delete', target_org='synthetic-org', wrapper_command='synthetic')
    ctx.org = org()
    assert ctx.acquire_org_lock() == 0
    alpha_lock = ctx._lock_path
    monkeypatch.setenv('TORQUE_WORKSPACE', str(tmp_path/'beta'))
    try:
        ctx.ensure_ownership()
        assert alpha_lock.exists()
        assert not (tmp_path/'beta').exists()
    finally:
        ctx.release_lock()
    assert not alpha_lock.exists()


def test_owner_loss_after_a_submitted_command_preserves_result_and_partial_status(tmp_path, monkeypatch):
    monkeypatch.setenv('TORQUE_WORKSPACE', str(tmp_path/'alpha'))
    ctx = common.WrapperContext(operation_type='data_bulk_delete', target_org='synthetic-org', wrapper_command='synthetic')
    ctx.org = org()
    assert ctx.acquire_org_lock() == 0
    monkeypatch.setattr(common.mf, 'get_sf_cli_version', lambda: 'synthetic')
    ctx.init_snapshot_dir()
    def run(*args, **kwargs):
        state = json.loads(ctx._lock_path.read_text(encoding="utf-8"))
        state['owner_token'] = 'other-owner'
        bundle.atomic_write_json(ctx._lock_path, state)
        return SimpleNamespace(returncode=0, stdout='synthetic submitted result', stderr='')
    monkeypatch.setattr(common.subprocess, 'run', run)
    try:
        with pytest.raises(org_sequence.LockOwnershipError):
            common.run_sf_subprocess(['sf','data','delete','bulk'])
    finally:
        with pytest.raises(org_sequence.LockOwnershipError):
            ctx.release_lock()
    assert json.loads((ctx.snap_dir/'manifest.json').read_text(encoding="utf-8"))['snapshot_status'] == 'partial'
    assert json.loads((ctx.snap_dir/'lease-lost-result.json').read_text(encoding="utf-8"))['stdout'] == 'synthetic submitted result'
