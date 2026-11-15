"""`torque browser login`: the printed expiry is one absolute deadline. No Salesforce
address is asked for or sent after it, and a client that connects and sends slowly or
not at all cannot stretch it.

Offline: the Salesforce CLI is a double; the only network use is numeric loopback.
Margins are seconds wide so that a loaded machine does not decide the result."""
from __future__ import annotations

import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from login_helpers import ADDRESS, MARKER, Command, FakeCli  # noqa: E402

from jsc_browser_tests import login, relay  # noqa: E402


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    FakeCli.reset()
    monkeypatch.setattr(login, "SfClient", FakeCli)
    monkeypatch.setattr(login.auth, "in_connected_mode", lambda: False)
    monkeypatch.setenv("TORQUE_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("TORQUE_TEST_USERS", raising=False)


def request_bytes(run: Command) -> bytes:
    return (f"GET {run.code_path} HTTP/1.1\r\nHost: 127.0.0.1:{run.port}\r\n"
            "Sec-Fetch-Mode: navigate\r\nSec-Fetch-Dest: document\r\n\r\n").encode("ascii")


def drip(run: Command, seconds: float, pieces: int = 16) -> bytes:
    """Send a valid navigation in `pieces` parts spread over `seconds`; return what came back."""
    data = request_bytes(run)
    size = -(-len(data) // pieces)
    connection = socket.create_connection(("127.0.0.1", run.port), timeout=10)
    try:
        for start in range(0, len(data), size):
            connection.sendall(data[start:start + size])
            time.sleep(seconds / pieces)
        return connection.recv(8192)
    except OSError:
        return b""        # the relay closed the connection: nothing was sent either
    finally:
        connection.close()


def test_a_request_finished_after_the_expiry_is_not_signed_in():
    run = Command("--target-org", "acme-sbx", "--timeout", "1", "--json")
    connection = socket.create_connection(("127.0.0.1", run.port), timeout=10)
    data = request_bytes(run)
    connection.sendall(data[:-2])
    time.sleep(2.0)
    try:
        connection.sendall(data[-2:])
        answer = connection.recv(8192)
    except OSError:
        answer = b""
    finally:
        connection.close()
    assert run.done() == login.EXIT_TIMEOUT and FakeCli.mints() == 0
    assert b"Location" not in answer and MARKER.encode() not in answer
    assert answer.startswith(b"HTTP/1.0 410"), "the browser is told the address expired"


def test_a_slow_client_cannot_extend_the_deadline():
    # Each piece arrives well within the old per-read timeout, so only an absolute
    # deadline ends this request at the printed expiry.
    run = Command("--target-org", "acme-sbx", "--timeout", "2", "--json")
    answer = drip(run, seconds=5)
    assert run.done() == login.EXIT_TIMEOUT and FakeCli.mints() == 0
    assert b"Location" not in answer and MARKER not in run.everything()


def test_one_connection_has_a_fixed_time_to_send_its_request(monkeypatch):
    monkeypatch.setattr(relay, "REQUEST_SECONDS", 1, raising=False)
    run = Command("--target-org", "acme-sbx", "--timeout", "60", "--json")
    answer = drip(run, seconds=3)
    assert b"Location" not in answer and FakeCli.mints() == 0
    status, headers, _ = run.get()        # the address is still unused and still works
    assert status == 302 and headers["Location"] == ADDRESS
    assert run.done() == login.EXIT_OK and FakeCli.mints() == 1


def test_an_idle_connection_does_not_hold_the_command_past_its_expiry():
    started = time.monotonic()
    run = Command("--target-org", "acme-sbx", "--timeout", "1", "--json")
    connection = socket.create_connection(("127.0.0.1", run.port), timeout=10)
    try:
        assert run.done() == login.EXIT_TIMEOUT
        ended = time.monotonic()
    finally:
        connection.close()
    assert ended - started < 4.0, "an idle connection held the relay past its expiry"
    assert FakeCli.mints() == 0


def test_an_address_the_cli_returns_after_the_expiry_is_dropped_unsent():
    FakeCli.reset(**{"acme-sbx": {"mint_seconds": 2.5}})
    run = Command("--target-org", "acme-sbx", "--timeout", "1", "--json")
    status, headers, body = run.get()
    assert status == 504 and "Location" not in headers and MARKER not in body
    assert run.done() == login.EXIT_TIMEOUT and FakeCli.mints() == 1
    failed = run.events()[-1]
    assert failed["event"] == "failed" and failed["exit_code"] == login.EXIT_TIMEOUT
    assert "dropped unsent and was not used" in failed["reason"]
    assert MARKER not in run.everything()


def test_the_printed_expiry_is_the_deadline():
    before = time.time()
    run = Command("--target-org", "acme-sbx", "--timeout", "30", "--json")
    waiting = run.events()[0]
    expires = datetime.strptime(waiting["expires_at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    assert before + 29 < expires.timestamp() < time.time() + 30.01
    run.get()
    assert run.done() == login.EXIT_OK
