"""On a laptop the agent shares the owner's account, so the gate's matchers are
the protection for engagement bindings and client control records."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from torque import engagements as eng
from torque import gate
from torque import gate_connected
from torque import workspace as ws


def setup(tmp_path):
    root = ws.init_workspace(tmp_path / "firm", "Synthetic firm")
    ws.add_client(root, "Alpha")
    eng.add_initiative(root, "Plan")
    return root


def test_binding_file_is_protected_in_every_mode(tmp_path):
    root = setup(tmp_path)
    binding = root / "initiatives" / "plan" / "binding.json"
    assert gate._protected_record(binding)
    assert gate._approval_file_reason("Write", {"file_path": str(binding)}, root)
    assert gate._approval_file_reason("Edit", {"file_path": "initiatives/plan/binding.json"}, root)


def test_removing_or_moving_an_initiative_folder_is_refused(tmp_path):
    root = setup(tmp_path)
    for command in ("rm -rf initiatives/plan", "mv initiatives/plan /tmp/x", "rm initiatives/plan/binding.json",
                    "cd initiatives && rm -rf plan"):
        assert gate._approval_file_reason("Bash", {"command": command}, root), command


def test_state_and_records_stay_writable(tmp_path):
    root = setup(tmp_path)
    for rel in ("initiatives/plan/state/engagement.json", "initiatives/plan/context.md",
                "initiatives/plan/sessions/x.json"):
        assert not gate._approval_file_reason("Write", {"file_path": rel}, root), rel


def test_future_client_control_folders_are_protected(tmp_path):
    root = setup(tmp_path)
    for name in ("control/consent.json", "requests/r.json", "claims/c.consumed", "binding.json"):
        assert gate._protected_record(root / "clients" / "alpha" / name), name


def test_connected_guard_covers_initiatives(tmp_path):
    root = setup(tmp_path)
    guarded = gate_connected._guarded(root, "alpha")
    assert root / "initiatives" in guarded
    assert all(p.name != "alpha" for p in guarded)


# --- Fix round 1: the protected-record check must block through _decide() itself,
# not only through _main()'s full-mode-only call, so build-only's decide() and
# connected mode's decide_connected() (which both call gate._decide() directly)
# are covered too. ---

BINDING_CASES = [
    ("Write", {"file_path": "initiatives/plan/binding.json", "content": "{}"}),
    ("Bash", {"command": "rm -rf initiatives/plan"}),
    ("Bash", {"command": "echo x > initiatives/plan/binding.json"}),
]


def test_binding_protected_in_build_only_mode(tmp_path):
    root = setup(tmp_path)
    for tool, inp in BINDING_CASES:
        allowed, reason = gate.decide(tool, inp, root, "build-only", root)
        assert not allowed and reason, (tool, inp)


def test_binding_protected_in_connected_mode_bound_and_unbound(tmp_path):
    root = setup(tmp_path)
    for bound in ("alpha", None):
        guarded = gate_connected._guarded(root, bound)
        for tool, inp in BINDING_CASES:
            allowed, reason = gate._decide(tool, inp, root, root, org_rules=False, guarded=guarded)
            assert not allowed and reason, (bound, tool, inp)


def test_bound_clients_own_control_records_protected_in_connected_mode(tmp_path):
    """clients/alpha is the bound client's own folder, so it is not in the guarded
    (other clients') list; only the record-level check keeps its consent, control,
    request, claim and binding records from a direct tool call."""
    root = setup(tmp_path)
    guarded = gate_connected._guarded(root, "alpha")
    for rel in ("clients/alpha/consent.json", "clients/alpha/control/x.json",
                "clients/alpha/requests/r.json", "clients/alpha/claims/c.consumed",
                "clients/alpha/binding.json"):
        allowed, reason = gate._decide("Write", {"file_path": rel, "content": "{}"},
                                       root, root, org_rules=False, guarded=guarded)
        assert not allowed and reason, rel
    # Ordinary notes in the bound client's own folder stay writable.
    allowed, reason = gate._decide("Write", {"file_path": "clients/alpha/notes.md", "content": "x"},
                                   root, root, org_rules=False, guarded=guarded)
    assert allowed, reason


def test_clients_index_count_ignores_tracked_initiative_files(tmp_path):
    """Ruling: counting initiatives/ in clients_index_count would lock the agent out
    of ordinary git commands (git log, git diff, git rm --cached) whenever an
    initiative file is tracked. initiatives/ is internal, not client-confidential,
    so it is deliberately excluded."""
    root = setup(tmp_path)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-f", "initiatives/plan/context.md"], check=True)
    assert gate.clients_index_count(root) == 0


CASE_VARIANTS = [
    ("Write", {"file_path": "Initiatives/plan/binding.json"}),
    ("Write", {"file_path": "initiatives/plan/BINDING.json"}),
    ("Write", {"file_path": "CLIENTS/alpha/consent.json"}),
]


def test_case_variant_paths_are_still_protected(tmp_path):
    """A case variant of a protected path reaches the same file on a
    case-insensitive filesystem; skip a variant only where this filesystem cannot
    actually reach it that way."""
    root = setup(tmp_path)
    (root / "clients" / "alpha" / "consent.json").write_text("{}", encoding="utf-8")
    checked = 0
    for tool, inp in CASE_VARIANTS:
        variant = root / Path(inp["file_path"])
        if not os.path.exists(str(variant)):
            continue
        checked += 1
        assert gate._approval_file_reason(tool, inp, root), (tool, inp)
    if not checked:
        pytest.skip("this filesystem is case-sensitive; no case variant reaches the file")


def test_case_variant_bash_removal_is_still_refused(tmp_path):
    root = setup(tmp_path)
    variant = root / "INITIATIVES" / "plan"
    if not os.path.exists(str(variant)):
        return
    assert gate._approval_file_reason("Bash", {"command": "rm -rf INITIATIVES/plan"}, root)


def test_worktree_copy_guards_initiatives_and_other_clients_through_a_symlinked_workspace(tmp_path):
    """The `copies` mapping in _decide compares realpaths, so a worktree copy's
    guarded folders map correctly even when the workspace itself is reached
    through a symlinked alias (not just its literal, lexical path)."""
    root = setup(tmp_path)
    ws.add_client(root, "Beta")
    real = Path(os.path.realpath(root))
    alias = tmp_path / "alias"
    alias.symlink_to(real)
    copy = real / ".claude" / "worktrees" / "w1"
    copy.mkdir(parents=True)
    shutil.copytree(real / "initiatives", copy / "initiatives")
    shutil.copytree(real / "clients", copy / "clients")

    guarded = gate_connected._guarded(alias, "alpha")
    other_client = gate._decide("Read", {"file_path": str(copy / "clients" / "beta" / "client.json")},
                                alias, alias, org_rules=False, guarded=guarded)
    assert not other_client[0]
    initiative = gate._decide("Read", {"file_path": str(copy / "initiatives" / "plan" / "context.md")},
                              alias, alias, org_rules=False, guarded=guarded)
    assert not initiative[0]
    own_client = gate._decide("Read", {"file_path": str(copy / "clients" / "alpha" / "client.json")},
                              alias, alias, org_rules=False, guarded=guarded)
    assert own_client[0]


# --- Fix round 2: `git rm --cached` only removes git's index entries; it never
# touches the working tree (regardless of -f/--force), so the record check must
# not treat it as removing a protected record. Otherwise the gate refuses its own
# prescribed remediation for a tracked clients/ (GIT_TRACKED_REASON tells the
# agent to run exactly `git rm -r --cached clients`), and the index count can
# never reach 0. ---

CACHED_GIT_RM_ALLOWED = [
    "git rm -r --cached clients",
    "git rm --cached clients/alpha/consent.json",
    "git rm -r --cached initiatives",
    "git rm --cached --force clients/alpha/consent.json",
    "git rm clients/alpha/consent.json --cached",
]
UNCACHED_GIT_RM_STILL_REFUSED = [
    "git rm clients/alpha/consent.json",
    "git rm -r clients",
]


def test_git_rm_cached_is_index_only_not_a_record_removal(tmp_path):
    root = setup(tmp_path)
    (root / "clients" / "alpha" / "consent.json").write_text("{}", encoding="utf-8")
    for command in CACHED_GIT_RM_ALLOWED:
        assert not gate._approval_file_reason("Bash", {"command": command}, root), command
    for command in UNCACHED_GIT_RM_STILL_REFUSED:
        assert gate._approval_file_reason("Bash", {"command": command}, root), command


def test_git_rm_cached_with_a_redirection_is_not_exempted(tmp_path):
    """`_is_cached_git_rm` requires the segment to hold no output redirection: a
    redirection tacked onto the same segment (e.g. into the very record it names)
    is a real working-tree write, not an index-only operation, so it stays refused."""
    root = setup(tmp_path)
    (root / "clients" / "alpha" / "consent.json").write_text("{}", encoding="utf-8")
    command = "git rm --cached clients/alpha/consent.json > clients/alpha/consent.json"
    assert gate._approval_file_reason("Bash", {"command": command}, root)


def test_cached_after_a_bare_double_dash_is_a_path_not_the_option(tmp_path):
    """After a bare `--`, git reads every following word as a pathspec, not an
    option: `git rm ... -- --cached` names a (usually nonexistent) path literally
    called --cached and performs a real, working-tree removal of whatever path came
    before it. The record check must not mistake this trailing `--cached` for the
    index-only option, or it would exempt (and an agent could use to delete) the
    named record."""
    root = setup(tmp_path)
    (root / "clients" / "alpha" / "consent.json").write_text("{}", encoding="utf-8")
    for command in ("git rm -f --ignore-unmatch clients/alpha/consent.json -- --cached",
                    "git rm -- clients/alpha/consent.json --cached"):
        assert gate._approval_file_reason("Bash", {"command": command}, root), command


def test_build_only_git_lockout_remediation_still_works(tmp_path):
    """End to end: while clients/ is tracked in git's index, build-only mode blocks
    ordinary git commands (GIT_TRACKED_REASON) until the agent runs the exact
    remediation it is told to run. The record check must not re-block that
    remediation, and running it for real must actually clear the lockout."""
    root = setup(tmp_path)
    (root / "clients" / "alpha" / "consent.json").write_text("{}", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-f", "clients/alpha/client.json",
                    "clients/alpha/consent.json"], check=True)
    assert gate.clients_index_count(root) == 2

    still_locked_out, reason = gate.decide("Bash", {"command": "git log"}, root, "build-only", root)
    assert not still_locked_out, "git log should still be blocked while clients/ is tracked"

    allowed, reason = gate.decide("Bash", {"command": "git rm -r --cached clients"}, root, "build-only", root)
    assert allowed, reason
    allowed2, reason2 = gate.decide("Bash", {"command": "git rm --cached clients/alpha/consent.json"},
                                    root, "build-only", root)
    assert allowed2, reason2

    subprocess.run(["git", "-C", str(root), "rm", "-r", "--cached", "-q", "clients"], check=True)
    assert gate.clients_index_count(root) == 0
    unblocked, reason3 = gate.decide("Bash", {"command": "git log"}, root, "build-only", root)
    assert unblocked, reason3


# --- Fix round 4: the `git rm --cached` exemption is a strict allowlist. git's
# option parsing (--end-of-options, --no-cached negation, --no-cache prefix
# abbreviation, a bare --, global options before rm) can turn a "cached"-looking
# command into a real working-tree delete, so only a fixed set of exact option
# words is accepted; anything else is judged as an ordinary remover. ---

STRICT_CACHED_REFUSED = [
    "git rm -f --ignore-unmatch --end-of-options clients/alpha/consent.json --cached",
    "git rm -f --ignore-unmatch clients/alpha/consent.json --end-of-options --cached",
    "git rm -f --cached --no-cached clients/alpha/consent.json",
    "git rm -f clients/alpha/consent.json --cached --no-cache",
    "git rm -f -- clients/alpha/consent.json --cached",
    "git rm -rf --cached clients",
    "git -C clients/alpha rm --cached consent.json",
]
STRICT_CACHED_ALLOWED = [
    "git rm -r --cached clients",
    "git rm --cached clients/alpha/consent.json",
    "git rm --cached --force clients/alpha/consent.json",
    "git rm clients/alpha/consent.json --cached",
    "git rm -r -q --cached clients",
    "git rm -r --cached initiatives",
]


def test_git_rm_cached_exemption_is_a_strict_allowlist(tmp_path):
    root = setup(tmp_path)
    (root / "clients" / "alpha" / "consent.json").write_text("{}", encoding="utf-8")
    for command in STRICT_CACHED_REFUSED:
        assert gate._approval_file_reason("Bash", {"command": command}, root), command
    for command in STRICT_CACHED_ALLOWED:
        assert not gate._approval_file_reason("Bash", {"command": command}, root), command


def test_allowed_cached_git_rm_forms_leave_the_file_on_disk(tmp_path):
    """Real git, in a throwaway repo: every exempted form only untracks."""
    for i, command in enumerate(STRICT_CACHED_ALLOWED):
        repo = tmp_path / f"r{i}"
        for rel in ("clients/alpha/consent.json", "initiatives/plan/binding.json"):
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text("{}", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "-f", "clients", "initiatives"], check=True)
        args = command.split()[1:]
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
        assert (repo / "clients/alpha/consent.json").is_file(), command
        assert (repo / "initiatives/plan/binding.json").is_file(), command


# --- Fix round 5: bash expansion can turn a word the gate reads as a path into an
# option (`$X`, `"$X"`, `$'--no-cached'`, `${X:---no-cached}`, braces, globs), so
# no exempted word may hold `$`, a backtick or any of `*?[]{}`. Words split off by
# a grouping character (`${`, `$(`, a backtick, `{a,b}`) still belong to the
# remover before them. ---

EXPANSION_REFUSED = [
    "X=--no-cached; git rm --cached $X clients/alpha/consent.json",
    "X=--no-cached; git rm --cached \"$X\" clients/alpha/consent.json",
    "git rm --cached $'--no-cached' clients/alpha/consent.json",
    "git rm --cached $'\\x2d-no-cached' clients/alpha/consent.json",
    "X=--no-cached; git rm -r --cached $X clients",
    "git rm -r --cached ${X:---no-cached} clients",
    "git rm --cached clients/alpha/*.json",
    # Same class, split across segments by a grouping character:
    "git rm --cached {--no-cached,clients/alpha/consent.json}",
    "git rm -r --cached {--no-cached,clients}",
    "git rm -r --cached $(printf -- --no-cached) clients",
    "git rm -r --cached `printf -- --no-cached` clients",
    "git rm --cached -f $'--no-cached' clients/alpha/consent.json",
    "rm -rf {x,clients}",
    "rm -rf $(true) clients",
]
EXPANSION_ALLOWED = [
    "git rm -r --cached clients",
    "git rm --cached clients/alpha/consent.json",
    "git rm clients/alpha/consent.json --cached",
]


def test_no_expansion_in_exempted_git_rm_cached_words(tmp_path):
    root = setup(tmp_path)
    (root / "clients" / "alpha" / "consent.json").write_text("{}", encoding="utf-8")
    for command in EXPANSION_REFUSED:
        assert gate._approval_file_reason("Bash", {"command": command}, root), command
    for command in EXPANSION_ALLOWED:
        assert not gate._approval_file_reason("Bash", {"command": command}, root), command


def test_expansion_forms_really_delete_and_allowed_forms_do_not(tmp_path):
    """Real bash + git in throwaway repos: every refused form deletes the record
    (or its folder), every allowed form leaves it on disk."""
    bash = shutil.which("bash")
    assert bash
    cases = [(c, True) for c in EXPANSION_REFUSED[:6] + EXPANSION_REFUSED[7:]] \
        + [(c, False) for c in EXPANSION_ALLOWED]
    for i, (command, deletes) in enumerate(cases):
        repo = tmp_path / f"e{i}"
        (repo / "clients" / "alpha").mkdir(parents=True)
        (repo / "clients" / "alpha" / "consent.json").write_text("{}", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "-f", "clients"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                        "commit", "-qm", "init"], check=True)
        subprocess.run([bash, "-c", command], cwd=repo, capture_output=True)
        assert (not (repo / "clients" / "alpha" / "consent.json").exists()) == deletes, command


# --- Final review: connected mode serves one client, never initiatives ---


def _connected_decide(root, command, bound):
    orig = gate_connected._bound_client
    gate_connected._bound_client = lambda env, w: bound
    try:
        return gate_connected.decide_connected("Bash", {"command": command.replace("W", str(root))}, root, root,
                                               env={}, permission_mode="default")
    finally:
        gate_connected._bound_client = orig


def test_connected_bound_session_cannot_list_every_engagement(tmp_path):
    root = setup(tmp_path)
    ws.add_client(root, "Beta")
    for command in ("torque engagement list --workspace W", "torque client list --workspace W"):
        assert _connected_decide(root, command, "alpha").action == "deny", command


def test_connected_bound_session_cannot_act_for_an_initiative(tmp_path):
    root = setup(tmp_path)
    for command in ("torque context --workspace W --initiative plan",
                    "torque context --workspace=W --initiative=plan",
                    "torque session add --workspace W --initiative plan --summary x",
                    "torque change note chg-000000000000 --workspace W --initiative plan --text x"):
        decision = _connected_decide(root, command, "alpha")
        assert decision.action == "deny", command
        assert "initiative" in decision.reason, command


def test_connected_bound_session_still_reaches_its_own_client(tmp_path):
    root = setup(tmp_path)
    assert _connected_decide(root, "torque context --workspace W --client alpha", "alpha").action == "allow"
