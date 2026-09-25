"""V2-5: `approval.delegated_path_patterns`, the expansion the trace test in
tests/test_delegated_reads.py checks a delegated grant against. That trace test
skips inside an AI session; this one does not, so the expansion itself (and in
particular that `{cwd_parent}` names only the folders on the way to the working
folder, never a sibling or anything below another folder) is proven everywhere."""
import fnmatch

from torque import approval


def matches(path, patterns):
    return any(fnmatch.fnmatch(path, p) for p in patterns)


def test_cwd_parent_expands_to_each_folder_on_the_way_to_the_working_folder():
    reads, writes = approval.delegated_path_patterns("acme", "clients/acme/cases/ax-01")
    for folder in ("clients", "clients/acme", "clients/acme/cases", "clients/acme/cases/ax-01"):
        assert folder in reads
    assert "{cwd_parent}" in approval.DELEGATED_READS
    assert not any("{" in p for p in reads + writes)
    assert writes == ["clients/acme/approvals/granted/*", "clients/acme/approvals/denied/*"]


def test_cwd_parent_names_no_sibling_and_nothing_inside_a_parent():
    reads, _ = approval.delegated_path_patterns("acme", "clients/acme/cases/ax-01")
    assert matches("clients/acme/cases", reads)
    assert not matches("clients/acme/cases/ax-02", reads)
    assert not matches("clients/acme/cases/notes.txt", reads)
    assert not matches("clients/acme/cases/ax-01-other", reads)
    assert matches("clients/acme/cases/ax-01/force-app/main/default/flows/x.flow-meta.xml", reads)


def test_a_working_folder_at_the_workspace_root_has_no_parents():
    reads, _ = approval.delegated_path_patterns("acme", "project")
    assert reads.count("project") == 1 and "." not in reads and "" not in reads


def test_an_absolute_working_folder_lists_its_absolute_parents():
    reads, _ = approval.delegated_path_patterns("acme", "/work/acme/project")
    assert "/work" in reads and "/work/acme" in reads and "/" not in reads
