"""A one-request sign-in relay on the loopback interface.

An agent that drives a browser tab cannot be handed a Salesforce sign-in address:
whatever a command prints reaches the model. So the command prints a local address
instead. When the browser tab opens it, this relay asks for the single-use Salesforce
address at that moment (`mint`) and answers with a redirect to it. The Salesforce
address is never printed, logged, stored or put in an exception here.

What keeps the local address from being more than a one-time handoff:

- it listens on 127.0.0.1 only, on a port the system picks, bound exclusively;
- the path is a long random code made here, compared in constant time;
- only a top-level browser navigation is answered (a fetch, a prefetch, curl or a
  script gets a refusal and nothing is asked of Salesforce);
- one valid request, then the listener closes; it also closes after too many wrong
  requests;
- one absolute deadline: no Salesforce address is asked for, and none is sent, after
  it. A connection is read only until the deadline (and for at most REQUEST_SECONDS
  in all), however slowly its bytes arrive;
- nothing about a request is logged.

Any process on this machine can reach a loopback port, whoever runs it, so the code is
the secret. A process of the same account can already ask the Salesforce CLI itself.
"""
from __future__ import annotations

import hmac
import io
import secrets
import socket
import socketserver
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from typing import Callable

CODE_BYTES = 24            # 32 URL-safe characters
MAX_WRONG_REQUESTS = 20
REQUEST_SECONDS = 5        # the most one connection may take to send its request, in all
NOT_A_NAVIGATION = ("Open this address in the browser tab itself. Other clients are not answered, "
                    "and nothing was signed in.")
EXPIRED = "This sign-in address expired; nothing was signed in. Run the command again for a new one."
DROPPED = ("the Salesforce CLI answered after the local address expired, so its sign-in address was dropped "
           "unsent and was not used (it is single-use and expires by itself); nothing was signed in")


@dataclass
class Minted:
    """What `mint` returns. The address is kept out of repr so that an accidental
    print, log line or traceback of this object cannot show it."""
    url: str = field(repr=False)
    org_id: str = ""
    username: str = ""


class RelayRefused(Exception):
    """`mint` raises this to refuse the redirect. `status` is one of the outcome
    statuses below and `reason` a sentence that is safe to print."""

    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status, self.reason = status, reason


@dataclass
class Outcome:
    """How the wait ended: "redirected", "timeout", "wrong_requests", "failed", or the
    status a RelayRefused named. `minted` is set only for "redirected"."""
    status: str
    reason: str = ""
    minted: Minted | None = None


class _Listener(socketserver.TCPServer):
    # Never share the port: socketserver's default already refuses reuse elsewhere, and
    # on Windows SO_REUSEADDR would let another process bind the same port.
    allow_reuse_address = False
    request_queue_size = 8

    def server_bind(self):
        if sys.platform == "win32":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request, client_address):
        pass  # a broken connection is not reported: the text could name the request

    def shutdown_request(self, request):
        # A connection cut off in the middle of its request was answered with a refusal;
        # its remaining bytes are read and dropped in the background before it is closed,
        # so that the refusal reaches the client instead of a connection reset. The relay
        # itself does not wait for it.
        if request is getattr(self, "drain", None):
            self.drain = None
            threading.Thread(target=_drain_and_close, args=(request,), daemon=True).start()
        else:
            super().shutdown_request(request)


def _drain_and_close(connection: socket.socket) -> None:
    until = time.monotonic() + REQUEST_SECONDS
    try:
        connection.shutdown(socket.SHUT_WR)
        while time.monotonic() < until:
            connection.settimeout(max(until - time.monotonic(), 0.01))
            if not connection.recv(4096):
                break
    except OSError:
        pass
    finally:
        connection.close()


class _ReadUntil(io.RawIOBase):
    """A connection's bytes, until a fixed moment. Each read may wait only for what is
    left, so a client that sends slowly cannot extend it; after it, reading is a timeout
    (which the HTTP handler answers with a refusal, logging nothing)."""

    def __init__(self, connection: socket.socket, until: float):
        self._connection, self._until = connection, until

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        left = self._until - time.monotonic()
        if left <= 0:
            raise TimeoutError("the request did not arrive in time")
        self._connection.settimeout(left)
        return self._connection.recv_into(buffer)


class Relay:
    """Bind first (so a bind failure is reported before anything is printed), print
    `address`, then `serve`."""

    def __init__(self, port: int = 0):
        self.code = secrets.token_urlsafe(CODE_BYTES)
        self._state: dict = {"outcome": None, "wrong": 0}
        self._server = _Listener(("127.0.0.1", port), self._handler(), bind_and_activate=True)
        self._server.timeout = 0.5
        self.port = self._server.server_address[1]

    @property
    def address(self) -> str:
        return f"http://127.0.0.1:{self.port}/{self.code}"

    def close(self) -> None:
        self._server.server_close()

    def serve(self, mint: Callable[[], Minted], *, timeout: float, deadline: float | None = None,
              max_wrong: int = MAX_WRONG_REQUESTS, navigation_only: bool = True) -> Outcome:
        """Wait for one browser navigation to the address, redirect it to what `mint`
        returns, and close. `mint` runs at most once. `deadline` (time.monotonic(), by
        default `timeout` seconds from now) is absolute: accepting, reading a request,
        asking for the address and sending it all end there."""
        deadline = time.monotonic() + timeout if deadline is None else deadline
        self._state.update(mint=mint, max_wrong=max_wrong, navigation_only=navigation_only, deadline=deadline)
        try:
            while self._state["outcome"] is None:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self._server.timeout = min(0.5, left)
                self._server.handle_request()
        finally:
            self.close()
        return self._state["outcome"] or Outcome(
            "timeout", f"no browser navigation reached the address within {int(timeout)} seconds")

    def _handler(self):
        relay = self

        class Handler(BaseHTTPRequestHandler):
            server_version, sys_version = "torque-login", ""
            timeout = REQUEST_SECONDS  # replaced in setup by what is left of the request's time

            def setup(self):
                # A client that connects and sends nothing, or sends slowly, holds the
                # listener at most REQUEST_SECONDS and never past the deadline.
                super().setup()
                self.until = min(time.monotonic() + REQUEST_SECONDS, relay._state["deadline"])
                self.rfile.close()
                self.rfile = io.BufferedReader(_ReadUntil(self.connection, self.until))
                self.answered = False
                # What a refusal needs when the request line itself never arrived.
                self.requestline, self.request_version, self.command = "", "HTTP/1.0", ""

            def send_response(self, *args, **kwargs):
                self.answered = True
                super().send_response(*args, **kwargs)

            def finish(self):
                # The request did not arrive in time (a client that closed at once is not answered).
                if not self.answered and getattr(self, "raw_requestline", None) != b"":
                    try:
                        self.connection.settimeout(1)
                        if self._expired() or self.until >= relay._state["deadline"]:
                            self._plain(410, EXPIRED)
                        else:
                            self._plain(408, "The request did not arrive in time; the address is still unused.")
                        self.server.drain = self.connection
                    except OSError:
                        pass
                super().finish()

            def log_message(self, *args):  # nothing about a request is logged
                pass

            def _expired(self) -> bool:
                return time.monotonic() >= relay._state["deadline"]

            def _plain(self, status: int, text: str) -> None:
                body = text.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _other_method(self):
                self._plain(405, "Only a browser navigation is answered.")
            do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _other_method

            def do_GET(self):
                state = relay._state
                if self.headers.get("Host", "").casefold() not in (f"127.0.0.1:{relay.port}", f"localhost:{relay.port}"):
                    return self._plain(421, "This address is answered on 127.0.0.1 only.")
                if state["outcome"] is not None:
                    return self._plain(410, "This sign-in address was already used.")
                asked = self.path.split("?", 1)[0].strip("/")
                if not hmac.compare_digest(asked.encode("utf-8", "replace"), relay.code.encode()):
                    state["wrong"] += 1
                    if state["wrong"] >= state["max_wrong"]:
                        state["outcome"] = Outcome("wrong_requests", "too many requests for a wrong address; the listener closed")
                    return self._plain(404, "Not found.")
                if self.headers.get("Sec-Purpose") or self.headers.get("Purpose") or self.headers.get("X-Moz"):
                    return self._plain(503, "A prefetch is not answered; the address is still unused.")
                if state["navigation_only"] and not (self.headers.get("Sec-Fetch-Mode") == "navigate"
                                                     and self.headers.get("Sec-Fetch-Dest") == "document"):
                    # curl, a script, a page's fetch or image request: nothing is asked of
                    # Salesforce and the address stays usable. Who started the navigation is
                    # not checked: a host tool may navigate from the page that is open.
                    return self._plain(400, NOT_A_NAVIGATION)
                if self._expired():  # nothing is asked of Salesforce after the deadline
                    state["outcome"] = Outcome("timeout", "the browser navigation arrived as the local address "
                                                          "expired; nothing was asked of Salesforce")
                    return self._plain(410, EXPIRED)
                try:
                    minted = state["mint"]()
                    if not isinstance(minted, Minted) or not minted.url.startswith("https://") \
                            or any(not "!" <= char <= "~" for char in minted.url):
                        # No address, or one that could not be sent as one header line.
                        raise RelayRefused("failed", "the Salesforce CLI returned no sign-in address")
                except RelayRefused as refused:
                    state["outcome"] = Outcome(refused.status, refused.reason)
                    return self._plain(502, "Not signed in: " + refused.reason)
                except Exception as exc:  # never the message: it could carry the address
                    state["outcome"] = Outcome("failed", f"the sign-in address could not be obtained ({type(exc).__name__})")
                    return self._plain(502, "Not signed in: the sign-in address could not be obtained.")
                if self._expired():  # the CLI was slow: the address is dropped, never sent
                    minted = None
                    state["outcome"] = Outcome("timeout", DROPPED)
                    return self._plain(504, EXPIRED)
                try:
                    self.send_response(302)
                    self.send_header("Location", minted.url)
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                except OSError:
                    state["outcome"] = Outcome("failed", "the browser closed the connection before the redirect was sent")
                    return
                state["outcome"] = Outcome("redirected", "", Minted("", minted.org_id, minted.username))

        return Handler
