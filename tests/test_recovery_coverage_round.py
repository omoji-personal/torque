"""Before-state mappings must agree across selectors and retrieve formats."""
import pytest

from torque import before_state as bs
from torque import approval


OBJECT_CHILDREN = [('CompactLayout', 'compactLayouts', 'compactLayout'), ('FieldSet', 'fieldSets', 'fieldSet'),
                   ('WebLink', 'webLinks', 'webLink'), ('BusinessProcess', 'businessProcesses', 'businessProcess'),
                   ('Index', 'indexes', 'index')]
CONTAINER_CHILDREN = [('AssignmentRule', 'assignmentRules', 'assignmentRules'),
                      ('AutoResponseRule', 'autoResponseRules', 'autoResponseRules'),
                      ('EscalationRule', 'escalationRules', 'escalationRules'),
                      ('MatchingRule', 'matchingRules', 'matchingRule'),
                      ('WorkflowOutboundMessage', 'workflows', 'workflow'), ('WorkflowTask', 'workflows', 'workflow')]
SIMPLE = [('CustomMetadata', 'customMetadata', 'md'), ('PermissionSetGroup', 'permissionsetgroups', 'permissionsetgroup'),
          ('Profile', 'profiles', 'profile'), ('QuickAction', 'quickActions', 'quickAction'),
          ('GlobalValueSet', 'globalValueSets', 'globalValueSet'), ('StandardValueSet', 'standardValueSets', 'standardValueSet'),
          ('CustomTab', 'tabs', 'tab'), ('CustomApplication', 'applications', 'app'), ('CustomPermission', 'customPermissions', 'customPermission')]


@pytest.mark.parametrize('component,path', [('SharingRules:Account', 'objects/Account.object'),
                                         ('Profile:Synthetic', 'layouts/Synthetic.layout')])
def test_unrelated_same_name_file_does_not_cover_component(component, path):
    assert bs.coverage([component], {'files': [{'path': path}]}) == [component]


@pytest.mark.parametrize('kind,folder,suffix', OBJECT_CHILDREN + CONTAINER_CHILDREN)
def test_child_coverage_in_both_formats(kind, folder, suffix):
    paths = ([f'objects/Account/{folder}/Synthetic.{suffix}-meta.xml', 'objects/Account.object']
             if (kind, folder, suffix) in OBJECT_CHILDREN else [f'{folder}/Account.{suffix}-meta.xml', f'{folder}/Account.{suffix}'])
    for path in paths:
        assert bs.coverage([f'{kind}:Account.Synthetic'], {'files': [{'path': path}]}) == []
    assert bs.coverage([f'{kind}:Case.Synthetic'], {'files': [{'path': paths[-1]}]})


@pytest.mark.parametrize('kind,folder,suffix', SIMPLE)
def test_common_source_maps_to_metadata_capture(kind, folder, suffix):
    component = bs.component_for_path(f'force-app/main/default/{folder}/Synthetic.{suffix}-meta.xml')
    assert component == f'{kind}:Synthetic'
    assert bs.coverage([component], {'files': [{'path': f'{folder}/Synthetic.{suffix}'}]}) == []


@pytest.mark.parametrize('relative,component', [
    ('lwc/synthetic/__tests__/synthetic.test.js', 'LightningComponentBundle:synthetic'),
    ('aura/synthetic/synthetic.cmp', 'AuraDefinitionBundle:synthetic'),
    ('objects/Account/fields/Synthetic__c.field-meta.xml', 'CustomField:Account.Synthetic__c'),
    *[(f'objects/Account/{folder}/Synthetic.{suffix}-meta.xml', f'{kind}:Account.Synthetic') for kind, folder, suffix in OBJECT_CHILDREN],
])
def test_narrow_source_directory_keeps_component_context(tmp_path, relative, component):
    cwd = tmp_path / 'lwc' / 'ancestor' / 'project'
    path = cwd / 'force-app' / 'main' / 'default' / relative
    path.parent.mkdir(parents=True)
    path.write_text('synthetic', encoding='utf-8')
    for selector in (path, path.parent):
        assert bs.deploy_components(['sf', 'project', 'deploy', 'start', '-d', str(selector)], cwd) == [component]


def test_new_deploy_options_and_repeated_assignments_bind_approval(tmp_path):
    deploy = 'torque deploy -o synthetic-org --metadata Flow:Synthetic --test-level RunSpecifiedTests --tests Synthetic_A --tests Synthetic_B --wait 5'
    assignment = 'torque org assign permset -o synthetic-org --name Synthetic_A --name Synthetic_B --on-behalf-of a@example.invalid --on-behalf-of b@example.invalid'
    for command, removed in [(deploy, '--tests Synthetic_A'), (deploy, '--wait 5'),
                             (assignment, '--name Synthetic_A'), (assignment, '--on-behalf-of a@example.invalid')]:
        assert approval.call_key_for_command(command) != approval.call_key_for_command(command.replace(removed, ''))
    manifest = tmp_path / 'post.xml'
    manifest.write_text('<Package><types><members>Synthetic</members><name>Flow</name></types></Package>', encoding='utf-8')
    argv = ['torque', 'deploy', '-o', 'synthetic-org', '--metadata', 'Flow:Synthetic', '--post-destructive-changes', str(manifest)]
    assert manifest in approval.payload_files(argv, tmp_path)
    assert bs.destructive_components(argv, tmp_path) == ['Flow:Synthetic']


def test_source_on_another_windows_drive_keeps_full_path_mapping(tmp_path, monkeypatch):
    path = tmp_path / 'objects' / 'Account' / 'fields' / 'Synthetic__c.field-meta.xml'
    path.parent.mkdir(parents=True)
    path.write_text('<CustomField/>', encoding='utf-8')
    def other_drive(*args):
        raise ValueError('path and working directory are on different drives')
    monkeypatch.setattr(bs.os.path, 'relpath', other_drive)
    assert bs.deploy_components(['sf', 'project', 'deploy', 'start', '-d', str(path.parent)], tmp_path) == ['CustomField:Account.Synthetic__c']
