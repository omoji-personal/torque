"""Every answer of the sign-in relay, the redirect and each refusal, is sent with
`Cache-Control: no-store`, `Referrer-Policy: no-referrer` and `X-Content-Type-Options:
nosniff`. Offline: the CLI is a double; the only network use is numeric loopback."""
from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from login_helpers import ADDRESS, NAVIGATION, Command, FakeCli  # noqa: E402

from jsc_browser_tests import login  # noqa: E402

EXPECTED = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    FakeCli.reset()
    monkeypatch.setattr(login, "SfClient", FakeCli)
    monkeypatch.setattr(login.auth, "in_connected_mode", lambda: False)
    monkeypatch.setenv("TORQUE_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("TORQUE_TEST_USERS", raising=False)


def has_the_headers(headers: dict) -> bool:
    return all(headers.get(name) == value for name, value in EXPECTED.items())


def test_the_redirect_and_every_refusal_carry_the_headers():
    run = Command("--target-org", "acme-sbx")
    answers = [run.get(path="/wrong"),                                   # 404
               run.get(host=f"rebound.example:{run.port}"),              # 421
               run.get(headers={"Sec-Fetch-Mode": "cors"}),              # 400, not a navigation
               run.get(headers={**NAVIGATION, "Sec-Purpose": "prefetch"}),  # 503
               run.get(method="POST")]                                   # 405
    assert [status for status, _, _ in answers] == [404, 421, 400, 503, 405]
    status, headers, _ = run.get()
    assert status == 302 and headers["Location"] == ADDRESS and has_the_headers(headers)
    assert all(has_the_headers(headers) for _, headers, _ in answers)
    assert run.done() == login.EXIT_OK


def test_an_expired_address_refusal_carries_them():
    FakeCli.reset(**{"acme-sbx": {"mint_seconds": 2.5}})
    run = Command("--target-org", "acme-sbx", "--timeout", "1")
    status, headers, _ = run.get()
    assert status == 504 and "Location" not in headers and has_the_headers(headers)
    assert run.done() == login.EXIT_TIMEOUT


def test_http_servers_own_error_page_carries_them_too():
    run = Command("--target-org", "acme-sbx", "--timeout", "5")
    with socket.create_connection(("127.0.0.1", run.port), timeout=10) as connection:
        connection.sendall(b"NOT A REQUEST LINE AT ALL\r\n\r\n")
        answer = connection.recv(8192).decode("latin-1")
    assert answer.startswith("HTTP/1.0 400")
    for name, value in EXPECTED.items():
        assert f"{name}: {value}" in answer
    run.get()
    run.done()
