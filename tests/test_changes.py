"""Real private-file continuity and bounded metadata observations, never live calls."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from torque import changes, workspace as ws
from jsc_qa.dispatcher import DispatchResult


@pytest.fixture
def firm(tmp_path):
    root = ws.init_workspace(tmp_path / 'firm', 'Synthetic consultants')
    ws.add_client(root, 'Alpha')
    ws.add_client(root, 'Beta')
    return root


def create(firm, client='Alpha'):
    return changes.create_change(firm, client, 'Contact preference', 'Staff can find the preference',
                                 ['Save a preference', 'Unassigned user cannot edit'], 'explicit-dev')


def test_handoff_continues_selected_business_context_and_retains_failed_history(firm, tmp_path):
    alpha, beta = create(firm), create(firm, 'Beta')
    changes.add_note(firm, 'Beta', beta['id'], 'BETA_PRIVATE_SENTINEL')
    changes.add_note(firm, 'Alpha', alpha['id'], 'Keep field optional', 'decision')
    proof = tmp_path / 'observed.txt'; proof.write_text('Alpha-only observation', encoding="utf-8")
    changes.add_check(firm, 'Alpha', alpha['id'], 'AC1', 'fail', 'Value did not persist', proof)
    changes.add_check(firm, 'Alpha', alpha['id'], 'AC1', 'pass', 'Reported recheck persisted value', proof)
    changes.add_note(firm, 'Alpha', alpha['id'], 'Run negative permission case', 'next_step')
    data = changes.get_change(firm, 'Alpha', alpha['id'])
    assert data['assessment']['reported_pass'] == 1
    assert data['assessment']['not_yet_reported_pass'] == ['AC2']
    assert data['assessment']['business_acceptance_independently_verified'] is False
    text = changes.render_change(firm, 'Alpha', alpha['id'])
    assert all(s in text for s in ['Value did not persist', 'Reported recheck', 'Keep field optional', 'Run negative'])
    assert 'BETA_PRIVATE_SENTINEL' not in text
    assert 'BETA_PRIVATE_SENTINEL' not in json.dumps(ws.get_context(firm, 'Alpha'))
    assert alpha['id'] in ws.render_handoff(firm, 'Alpha')


def test_evidence_is_captured_then_changed_or_missing_copy_is_visible(firm, tmp_path):
    item = create(firm)
    original = tmp_path / 'proof.txt'; original.write_text('Captured once', encoding="utf-8")
    event = changes.add_check(firm, 'Alpha', item['id'], 'AC1', 'pass', 'Human reports success', original)
    root, _ = changes.load_change(firm, 'Alpha', item['id'])
    copied = root / event['evidence']['path']
    assert copied.read_text(encoding="utf-8") == 'Captured once'
    original.write_text('Changed at original source', encoding="utf-8")
    assert changes.get_change(firm, 'Alpha', item['id'])['events'][0]['evidence_integrity'] == 'matches_capture'
    copied.write_text('Tampered copied evidence', encoding="utf-8")
    data = changes.get_change(firm, 'Alpha', item['id'])
    assert data['events'][0]['evidence_integrity'] == 'changed'
    assert data['assessment']['evidence_problems'] == 1
    copied.unlink()
    assert changes.get_change(firm, 'Alpha', item['id'])['events'][0]['evidence_integrity'] == 'missing'


def test_cross_client_and_symlink_evidence_cannot_leak(firm, tmp_path):
    item = create(firm)
    beta = firm / 'clients/beta/context.md'
    link = tmp_path / 'link'; link.symlink_to(beta)
    for source in (beta, link):
        with pytest.raises(ws.WorkspaceError):
            changes.add_check(firm, 'Alpha', item['id'], 'AC1', 'pass', 'Bad reference', source)
    with pytest.raises(ws.WorkspaceError):
        changes.get_change(firm, 'Beta', item['id'])
    assert changes.get_change(firm, 'Alpha', item['id'])['events'] == []


def test_no_criteria_never_implies_acceptance_and_invalid_input_writes_nothing(firm):
    for title, outcome, criteria in [('', 'outcome', []), ('title', '', []), ('title', 'outcome', [''])]:
        with pytest.raises(ws.WorkspaceError):
            changes.create_change(firm, 'Alpha', title, outcome, criteria)
    assert changes.list_changes(firm, 'Alpha') == []
    item = changes.create_change(firm, 'Alpha', 'Draft', 'Unspecified approach')
    data = changes.get_change(firm, 'Alpha', item['id'])
    assert data['assessment']['criteria'] == 0
    assert data['assessment']['business_acceptance_independently_verified'] is False
    with pytest.raises(ws.WorkspaceError):
        changes.add_check(firm, 'Alpha', item['id'], 'AC1', 'pass', 'invented criterion')


def test_concurrent_event_writers_preserve_every_decision(firm):
    item = create(firm)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda n: changes.add_note(firm, 'Alpha', item['id'], f'Decision {n}', 'decision'), range(16)))
    data = changes.get_change(firm, 'Alpha', item['id'])
    assert len(data['events']) == len({e['id'] for e in data['events']}) == 16
    root, _ = changes.load_change(firm, 'Alpha', item['id'])
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in (root / 'events').glob('*.json'))


def test_live_deploy_observation_keeps_exact_scope_and_cannot_pass_business_criteria(firm):
    item = create(firm)
    job = '0Af000000000001AAA'
    response = DispatchResult('MetaAPI', 'PASS', 'Exact validation succeeded', raw_output='{"checkOnly":true}',
                              metadata={'operation': 'validation', 'job_id': job})
    with patch('jsc_qa.dispatcher.dispatch_meta_api', return_value=response) as dispatch:
        event = changes.verify_deploy(firm, 'Alpha', item['id'], 'explicit-dev', job, ['CustomField:Account.X__c'])
    assert dispatch.call_args.kwargs['deploy_job_id'] == job
    assert dispatch.call_args.args[0] == 'explicit-dev'
    assert event['kind'] == 'metadata_observation' and event['business_acceptance_proven'] is False
    assert event['observation']['metadata']['operation'] == 'validation'
    assert 'raw_output' not in event['observation']
    data = changes.get_change(firm, 'Alpha', item['id'])
    assert data['assessment']['reported_pass'] == 0
    assert data['assessment']['not_yet_reported_pass'] == ['AC1', 'AC2']
    assert data['events'][0]['evidence_integrity'] == 'matches_capture'


def test_failed_metadata_observation_is_retained_as_failure(firm):
    item = create(firm)
    with patch('jsc_qa.dispatcher.dispatch_meta_api', return_value=DispatchResult('MetaAPI', 'FAIL', 'Component missing')):
        event = changes.verify_deploy(firm, 'Alpha', item['id'], 'dev', '0Af000000000001AAA', ['CustomField:Account.X__c'])
    assert event['result'] == 'fail'
    assert 'Component missing' in changes.render_change(firm, 'Alpha', item['id'])


def test_tampered_event_or_evidence_path_is_rejected(firm):
    item = create(firm)
    event = changes.add_check(firm, 'Alpha', item['id'], 'AC1', 'pass', 'Reported')
    root, _ = changes.load_change(firm, 'Alpha', item['id'])
    path = root / 'events' / (event['id'] + '.json')
    event['evidence'] = {'path': '../../beta/context.md', 'sha256': 'made-up'}
    path.write_text(json.dumps(event), encoding="utf-8")
    with pytest.raises(ws.WorkspaceError):
        changes.get_change(firm, 'Alpha', item['id'])
    event['evidence'] = None; event['basis'] = 'independently_verified'
    path.write_text(json.dumps(event), encoding="utf-8")
    with pytest.raises(ws.WorkspaceError):
        changes.get_change(firm, 'Alpha', item['id'])


@pytest.mark.parametrize('field,value', [('created_at', None), ('created_at', 'not-a-date'),
    ('created_at', '2026-09-07T12:00:00'), ('title', ' '), ('outcome', ''), ('planned_org', ['wrong-type'])])
def test_malformed_record_is_an_actionable_error_instead_of_a_traceback(firm, field, value):
    item = create(firm)
    root, _ = changes.load_change(firm, 'Alpha', item['id'])
    item[field] = value
    (root / 'change.json').write_text(json.dumps(item), encoding="utf-8")
    with pytest.raises(ws.WorkspaceError, match='invalid change'):
        changes.list_changes(firm, 'Alpha')


def test_handoff_reports_manifest_drift_and_exact_component_scope(firm, tmp_path):
    item = create(firm)
    manifest = tmp_path / 'package.xml'; manifest.write_text('<Package/>', encoding="utf-8")
    component = 'CustomField:Account.Contact_Preference__c'
    with patch('jsc_qa.dispatcher.dispatch_meta_api', return_value=DispatchResult('MetaAPI', 'PASS', 'Exact job succeeded')):
        event = changes.verify_deploy(firm, 'Alpha', item['id'], 'dev', '0Af000000000001AAA', [component], manifest)
    root, _ = changes.load_change(firm, 'Alpha', item['id'])
    (root / event['manifest']['path']).write_text('changed after observation', encoding="utf-8")
    data = changes.get_change(firm, 'Alpha', item['id'])
    assert data['assessment']['evidence_problems'] == 1
    report = changes.render_change(firm, 'Alpha', item['id'])
    assert component in report and 'Deployment manifest: changed' in report
