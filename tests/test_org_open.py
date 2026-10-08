"""D17: `sf org open` (and `sfdx force:org:open`) mint a session URL, so every form
of it is a gated org write, never an "unverifiable" ask-and-hope. Covers the plain
forms, every flag spelling the CLI documents, and the wrapped shapes (env, exec,
xargs, bash -c, python -c, find -exec) the a15 classifier already understands for
every other `sf` write.

A form that prints that URL to the session (--url-only, -r, --json) is not a write
an approval can cover: it is refused as a credential-printing command
(tests/test_connected_credentials.py). The forms below only open a browser."""
import pytest

from torque import connected_routes as cr, permissions

K = lambda tool, inp: [(r.kind, r.org) for r in cr.classify(tool, inp)]
B = lambda cmd: K("Bash", {"command": cmd})


@pytest.fixture(autouse=True)
def no_container_mode(monkeypatch):
    # In container mode every `sf org open` prints its URL, so every form is refused.
    for name in cr.SF_CONTAINER_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("command", [
    "sf org open -o acme-dev", "sf org open --target-org acme-dev",
    "sf org open --path /lightning/setup/SetupOneHome/home -o acme-dev", "sf org open --browser chrome -o acme-dev",
    "sf org open --private -o acme-dev", "sf org open -o acme-dev --private -b firefox",
    "sfdx force:org:open -u acme-dev", "sfdx force:org:open -u acme-dev -p /lightning/setup/SetupOneHome/home",
])
def test_org_open_is_a_gated_write(command):
    routes = cr.classify("Bash", {"command": command})
    assert [(r.kind, r.org) for r in routes] == [("org_write", "acme-dev")]


@pytest.mark.parametrize("command", [
    "sf org open --target-org acme-dev --url-only", "sf org open -o acme-dev -r",
    "sf org open -o acme-dev --private -r", "sf org open -ro acme-dev", "sf org open -o acme-dev --urlonly", "sf org open -o acme-dev --json",
    "sfdx force:org:open -u acme-dev -r", "sfdx force:org:open -u acme-dev --json",
])
def test_org_open_that_prints_its_url_is_refused_not_gated(command):
    # Was a gated write (and so approvable) before; it hands the session a login URL.
    assert B(command) == [("credential", None)]


def test_org_open_without_an_org_is_refused():
    assert [r.kind for r in cr.classify("Bash", {"command": "sf org open"})] == ["no_org"]


def test_org_open_without_an_org_is_refused_url_only():
    # A flag alone (no --target-org / -o) still refuses, now as a credential-printing
    # command: the URL it prints would be the default org's.
    assert [r.kind for r in cr.classify("Bash", {"command": "sf org open --url-only"})] == ["credential"]


def test_interactive_profile_asks_on_org_open():
    assert "Bash(sf org open:*)" in permissions.generate("interactive")["ask"]
    assert "Bash(sf org open:*)" not in permissions.generate("unattended")["ask"]


# Wrapped forms: the same shapes tests/test_connected_routes.py already proves for
# `sf apex run` and other SF_WRITE_PREFIXES entries. `sf org open` must not get a
# different (weaker) answer just because it is wrapped.

@pytest.mark.parametrize("cmd,expected", [
    ("env sf org open -o acme-dev", [("org_write", "acme-dev")]),
    ("env HOME=/tmp/x sf org open -o acme-dev", [("unverifiable", "acme-dev")]),
    ("xargs sf org open -o acme-dev", [("unverifiable", None), ("org_write", "acme-dev")]),
    ("bash -c 'sf org open -o acme-dev'", [("unverifiable", None), ("org_write", "acme-dev")]),
    ("python3 -c 'import os; os.system(\"sf org open -o acme-dev\")'", [("unverifiable", None)]),
    ("find . -name x -exec sf org open -o acme-dev \\;", [("unverifiable", None), ("org_write", "acme-dev")]),
    ("nohup sf org open -o acme-dev", [("org_write", "acme-dev")]),
    ("eval 'sf org open -o acme-dev'", [("unverifiable", None), ("org_write", "acme-dev")]),
    ("echo $(sf org open -o acme-dev)", [("local", None), ("org_write", "acme-dev")]),
])
def test_wrapped_org_open_forms_stay_gated(cmd, expected):
    assert B(cmd) == expected


def test_sfdx_alias_is_also_gated():
    assert B("sfdx force:org:open -u acme-dev") == [("org_write", "acme-dev")]
