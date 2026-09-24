import io
import os

import pytest

from torque import presence


class Tty(io.StringIO):
    def __init__(self, text="", tty=True):
        super().__init__(text)
        self._tty = tty

    def isatty(self):
        return self._tty


def check(env=None, stdin=None, stdout=None, ancestors=lambda: [(1, "login")]):
    return presence.operator_present(env={} if env is None else env, stdin=stdin or Tty(), stdout=stdout or Tty(),
                                     ancestors=ancestors)


def test_requires_real_terminal():
    result = check(stdin=Tty(tty=False))
    assert not result.ok and "terminal" in result.reason
    assert not check(stdout=Tty(tty=False)).ok


@pytest.mark.parametrize("name", presence.AGENT_ENV)
def test_refuses_agent_environment(name):
    result = check(env={name: "1"})
    assert not result.ok and "agent" in result.reason


def test_empty_agent_variable_is_not_a_marker():
    assert check(env={"CLAUDECODE": ""}).ok


@pytest.mark.skipif(os.name == "nt", reason="ancestry is checked on macOS and Linux")
def test_refuses_agent_ancestor():
    result = check(ancestors=lambda: [(10, "zsh"), (9, "/usr/local/bin/claude")])
    assert not result.ok and "claude" in result.reason


@pytest.mark.skipif(os.name == "nt", reason="ancestry is checked on macOS and Linux")
def test_unknown_ancestry_fails_closed():
    assert not check(ancestors=lambda: [(-1, "<unknown>")]).ok


def test_present_operator():
    assert check().ok


def test_confirm_code_matches_typed_text():
    picks = iter("ABCDEF")
    out = Tty()
    assert presence.confirm_code(stdin=Tty("abcdef\n"), stdout=out, choose=lambda _: next(picks))
    assert "ABCDEF" in out.getvalue()


def test_confirm_code_rejects_other_text():
    picks = iter("ABCDEF")
    assert not presence.confirm_code(stdin=Tty("yes\n"), stdout=Tty(), choose=lambda _: next(picks))


def test_confirm_code_rejects_end_of_input():
    picks = iter("ABCDEF")
    assert not presence.confirm_code(stdin=Tty(""), stdout=Tty(), choose=lambda _: next(picks))


def test_code_is_random_by_default():
    codes = set()
    for _ in range(5):
        out = Tty()
        presence.confirm_code(stdin=Tty("x\n"), stdout=out)
        codes.add(out.getvalue())
    assert len(codes) > 1


@pytest.mark.skipif(os.name == "nt", reason="ps ancestry is macOS and Linux only")
def test_real_ancestry_is_readable():
    chain = presence._process_ancestors()
    assert chain and all(isinstance(pid, int) for pid, _ in chain)
