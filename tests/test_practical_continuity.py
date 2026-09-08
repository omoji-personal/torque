"""Practical onboarding/handoff/customization regressions; all data is synthetic."""
from concurrent.futures import ThreadPoolExecutor
import contextlib
import io
import json
import multiprocessing
import os
from pathlib import Path
import threading

import pytest

from torque import changes, cli, template_updates as updates, workspace as ws


def invoke(*args):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(list(args))
    return code, stdout.getvalue(), stderr.getvalue()


@pytest.fixture
def firm(tmp_path, monkeypatch):
    monkeypatch.delenv('TORQUE_WORKSPACE', raising=False)
    root = ws.init_workspace(tmp_path / 'firm', 'Synthetic firm')
    alpha = ws.add_client(root, 'Alpha')
    beta = ws.add_client(root, 'Beta')
    return root, alpha, beta


@pytest.mark.parametrize('failure_point', ['config', 'context', 'publication'])
def test_failed_new_client_leaves_no_occupied_slug_and_retry_works(firm, monkeypatch, failure_point):
    root, alpha, beta = firm
    existing = {path: path.read_bytes() for client in (alpha, beta) for path in client.rglob('*') if path.is_file()}
    original_write, original_atomic, original_rename = ws._write_json, ws.atomic_write_new, Path.rename
    def fail_config(path, value):
        if path.name == 'client.json': raise OSError('Synthetic configuration write failure')
        return original_write(path, value)
    def fail_context(path, text):
        if path.name == 'context.md': raise OSError('Synthetic context write failure')
        return original_atomic(path, text)
    def fail_publish(path, target):
        if Path(target).name == 'new-client': raise OSError('Synthetic publication failure')
        return original_rename(path, target)
    with monkeypatch.context() as patcher:
        if failure_point == 'config': patcher.setattr(ws, '_write_json', fail_config)
        elif failure_point == 'context': patcher.setattr(ws, 'atomic_write_new', fail_context)
        else: patcher.setattr(Path, 'rename', fail_publish)
        assert invoke('client', 'add', 'New Client', '--workspace', str(root))[0] == 2
    assert not (root / 'clients/new-client').exists()
    assert {c['slug'] for c in ws.list_clients(root)} == {'alpha', 'beta'}
    assert list((root / '.torque/client-staging').iterdir()) == []
    for path, contents in existing.items(): assert path.read_bytes() == contents
    assert invoke('client', 'add', 'New Client', '--workspace', str(root))[0] == 0
    created, _, config = ws.load_client(root, 'New Client')
    assert config['slug'] == 'new-client' and (created / 'context.md').is_file()


def _exit_during_client_creation(root):
    original = ws._write_json
    def crash(path, data):
        if path.name == 'client.json': os._exit(86)
        return original(path, data)
    ws._write_json = crash
    ws.add_client(root, 'Interrupted')


def test_process_termination_leaves_only_staging_and_releases_creation_lock(firm):
    root, _, _ = firm
    process = multiprocessing.get_context('fork').Process(target=_exit_during_client_creation, args=(root,))
    process.start(); process.join(10)
    if process.is_alive(): process.kill(); process.join(); pytest.fail('Synthetic child did not finish')
    assert process.exitcode == 86
    assert not (root / 'clients/interrupted').exists()
    abandoned = set((root / '.torque/client-staging').iterdir())
    assert len(abandoned) == 1
    assert ws.add_client(root, 'Interrupted').is_dir()
    assert set((root / '.torque/client-staging').iterdir()) == abandoned


def test_existing_client_content_is_never_replaced(firm):
    root, alpha, _ = firm
    sentinel = alpha / 'artifacts/working.txt'; sentinel.write_text('SYNTHETIC_EXISTING_WORK')
    before = {p: p.read_bytes() for p in alpha.rglob('*') if p.is_file()}
    assert invoke('client', 'add', 'ALPHA', '--workspace', str(root))[0] == 2
    assert all(p.read_bytes() == value for p, value in before.items())


def test_concurrent_same_slug_publishes_exactly_one_complete_client(firm):
    root, _, _ = firm
    ready = threading.Barrier(2)
    def add():
        ready.wait(timeout=5)
        try: return str(ws.add_client(root, 'Concurrent'))
        except ws.WorkspaceError: return 'collision'
    with ThreadPoolExecutor(max_workers=2) as executor: results = list(executor.map(lambda _: add(), range(2)))
    assert results.count('collision') == 1
    client, _, config = ws.load_client(root, 'Concurrent')
    assert config['name'] == 'Concurrent' and (client / 'context.md').is_file()
    assert len(ws.list_clients(root)) == 3
    assert list((root / '.torque/client-staging').iterdir()) == []


def test_client_discovery_never_sees_staging_with_published_config(firm, monkeypatch):
    root, _, _ = firm
    waiting, release = threading.Event(), threading.Event()
    original = ws.atomic_write_new
    def pause(path, text):
        if path.name == 'context.md' and path.parent.name.startswith('discoverable-'):
            waiting.set()
            assert release.wait(timeout=5)
        return original(path, text)
    monkeypatch.setattr(ws, 'atomic_write_new', pause)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(ws.add_client, root, 'Discoverable')
        try:
            assert waiting.wait(timeout=5)
            assert {c['slug'] for c in ws.list_clients(root)} == {'alpha', 'beta'}
        finally: release.set()
        future.result(timeout=5)
    assert {c['slug'] for c in ws.list_clients(root)} == {'alpha', 'beta', 'discoverable'}


def test_handoff_includes_same_bounded_notes_as_context_without_sibling_data(firm):
    root, alpha, beta = firm
    (root / 'profile.md').write_text('SYNTHETIC_FIRM_CONVENTION')
    (alpha / 'context.md').write_text('SYNTHETIC_ALPHA_SCOPE')
    (alpha / 'context/decision.md').write_text('SYNTHETIC_ALPHA_DECISION')
    (alpha / 'context/long.md').write_text('A' * 65536 + 'SYNTHETIC_TRUNCATED_TAIL')
    (beta / 'context.md').write_text('SYNTHETIC_BETA_PRIVATE')
    ws.add_session(root, 'Alpha', 'SYNTHETIC_JOURNAL', 'incomplete')
    item = changes.create_change(root, 'Alpha', 'Synthetic change', 'SYNTHETIC_CHANGE_OUTCOME', ['Save'])
    changes.add_check(root, 'Alpha', item['id'], 'AC1', 'fail', 'SYNTHETIC_REPORTED_FAILURE')
    target = alpha / 'artifacts/handoff.md'
    assert invoke('handoff', '--workspace', str(root), '--client', 'Alpha', '--output', str(target))[0] == 0
    body = target.read_text()
    for text in ws.get_context(root, 'Alpha')['notes'].values(): assert text.rstrip() in body
    assert '[truncated at 65,536 characters]' in body and 'SYNTHETIC_TRUNCATED_TAIL' not in body
    assert 'SYNTHETIC_BETA_PRIVATE' not in body
    assert all(x in body for x in ('SYNTHETIC_JOURNAL', 'SYNTHETIC_CHANGE_OUTCOME', 'SYNTHETIC_REPORTED_FAILURE', 'user-reported', 'operator-reported'))


def test_multibyte_context_and_handoff_use_character_limit_without_sibling_data(firm):
    root, alpha, beta = firm
    contents = '界' * 65536
    marker = '[truncated at 65,536 characters]'
    (alpha / 'context/multibyte.md').write_text(contents + 'SYNTHETIC_MULTIBYTE_TAIL')
    (beta / 'context.md').write_text('SYNTHETIC_BETA_PRIVATE')
    notes = ws.get_context(root, 'Alpha')['notes']
    assert notes['Client context: multibyte.md'] == contents + '\n' + marker
    assert len(contents.encode('utf-8')) > 65536
    body = ws.render_handoff(root, 'Alpha')
    assert contents + '\n' + marker in body
    assert 'SYNTHETIC_MULTIBYTE_TAIL' not in body and 'SYNTHETIC_BETA_PRIVATE' not in body
    assert '64 KiB' not in body


def test_handoff_rejects_notes_symlink_before_export(firm):
    root, alpha, beta = firm
    (beta / 'context.md').write_text('SYNTHETIC_BETA_SECRET')
    (alpha / 'context.md').unlink()
    (alpha / 'context.md').symlink_to(beta / 'context.md')
    target = alpha / 'artifacts/handoff.md'
    code, output, error = invoke('handoff', '--workspace', str(root), '--client', 'Alpha', '--output', str(target))
    assert code == 2 and 'symlink' in error and not target.exists()
    assert 'SYNTHETIC_BETA_SECRET' not in output + error


def test_handoff_allows_missing_optional_notes(firm):
    root, alpha, _ = firm
    (root / 'profile.md').unlink(); (alpha / 'context.md').unlink(); (alpha / 'context').rmdir()
    body = ws.render_handoff(root, 'Alpha')
    assert 'No session entries' in body and 'Working context' not in body


def test_workflow_show_prefers_preserved_customization_in_selected_workspace(firm):
    root, _, _ = firm
    local = root / '.claude/commands/qa.md'; local.write_text('SYNTHETIC_FIRM_QA_CUSTOMIZATION')
    report = updates.update_templates(root)
    assert next(a for a in report['actions'] if a['path'] == '.claude/commands/qa.md')['action'] == 'preserve'
    code, output, error = invoke('workflows', 'show', 'qa', '--workspace', str(root))
    assert code == 0 and error == '' and output.strip() == local.read_text()
    assert 'torque workflows show NAME --workspace .' in (root / 'AGENTS.md').read_text()


def test_workflow_show_missing_local_recipe_uses_packaged_fallback(firm):
    root, _, _ = firm
    (root / '.claude/commands/qa.md').unlink()
    unselected = invoke('workflows', 'show', 'qa')
    selected = invoke('workflows', 'show', 'qa', '--workspace', str(root))
    assert selected == unselected and selected[0] == 0


def test_unselected_workflow_show_does_not_infer_workspace_from_cwd(firm, monkeypatch):
    root, _, _ = firm
    local = root / '.claude/commands/qa.md'; local.write_text('SYNTHETIC_CUSTOM_LOCAL')
    monkeypatch.chdir(root)
    code, output, _ = invoke('workflows', 'show', 'qa')
    assert code == 0 and 'SYNTHETIC_CUSTOM_LOCAL' not in output


@pytest.mark.parametrize('client_scope', [False, True])
def test_workflow_show_honors_existing_explicit_environment_scope(firm, monkeypatch, client_scope):
    root, alpha, _ = firm
    (root / '.claude/commands/qa.md').write_text('SYNTHETIC_SELECTED_ENV_RECIPE')
    monkeypatch.setenv('TORQUE_WORKSPACE', str(alpha if client_scope else root))
    assert invoke('workflows', 'show', 'qa')[1].strip() == 'SYNTHETIC_SELECTED_ENV_RECIPE'


def test_explicit_workflow_workspace_wins_over_inherited_selection(firm, tmp_path, monkeypatch):
    root, _, _ = firm
    other = ws.init_workspace(tmp_path / 'other', 'Other synthetic firm')
    (other / '.claude/commands/qa.md').write_text('SYNTHETIC_OTHER_FIRM')
    (root / '.claude/commands/qa.md').write_text('SYNTHETIC_SELECTED_FIRM')
    monkeypatch.setenv('TORQUE_WORKSPACE', str(other))
    code, output, _ = invoke('workflows', 'show', 'qa', '--workspace', str(root))
    assert code == 0 and output.strip() == 'SYNTHETIC_SELECTED_FIRM'


def test_workflow_catalogue_json_shape_is_unchanged(firm):
    root, _, _ = firm
    baseline = invoke('workflows', 'show', 'qa', '--json')
    selected = invoke('workflows', 'show', 'qa', '--workspace', str(root), '--json')
    assert baseline == selected and isinstance(json.loads(selected[1]), dict)


def test_invalid_selected_workspace_does_not_silently_use_packaged_recipe(tmp_path):
    code, output, error = invoke('workflows', 'show', 'qa', '--workspace', str(tmp_path / 'absent'))
    assert code == 2 and output == '' and 'configuration' in error


def test_workflow_symlink_does_not_read_other_workspace(firm, tmp_path):
    root, _, _ = firm
    other = tmp_path / 'private.txt'; other.write_text('SYNTHETIC_OTHER_PRIVATE')
    recipe = root / '.claude/commands/qa.md'; recipe.unlink(); recipe.symlink_to(other)
    code, output, error = invoke('workflows', 'show', 'qa', '--workspace', str(root))
    assert code == 2 and 'symlink' in error and 'SYNTHETIC_OTHER_PRIVATE' not in output + error


def test_empty_local_workflow_is_not_replaced_by_packaged_fallback(firm):
    root, _, _ = firm
    (root / '.claude/commands/qa.md').write_text('')
    assert invoke('workflows', 'show', 'qa', '--workspace', str(root)) == (0, '\n', '')
