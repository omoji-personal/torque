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
- one valid request, then the listener closes; it also closes at the timeout and
  after too many wrong requests;
- nothing about a request is logged.

Any process on this machine can reach a loopback port, whoever runs it, so the code is
the secret. A process of the same account can already ask the Salesforce CLI itself.
"""
from __future__ import annotations

import hmac
import secrets
import socket
import socketserver
import sys
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from typing import Callable

CODE_BYTES = 24            # 32 URL-safe characters
MAX_WRONG_REQUESTS = 20
NOT_A_NAVIGATION = ("Open this address in the browser tab itself. Other clients are not answered, "
                    "and nothing was signed in.")


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

    def serve(self, mint: Callable[[], Minted], *, timeout: float, max_wrong: int = MAX_WRONG_REQUESTS,
              navigation_only: bool = True) -> Outcome:
        """Wait for one browser navigation to the address, redirect it to what `mint`
        returns, and close. `mint` runs at most once."""
        self._state.update(mint=mint, max_wrong=max_wrong, navigation_only=navigation_only)
        deadline = time.monotonic() + timeout
        try:
            while self._state["outcome"] is None and time.monotonic() < deadline:
                self._server.handle_request()
        finally:
            self.close()
        return self._state["outcome"] or Outcome("timeout", f"no browser opened the address within {int(timeout)} seconds")

    def _handler(self):
        relay = self

        class Handler(BaseHTTPRequestHandler):
            server_version, sys_version = "torque-login", ""
            timeout = 5  # a client that connects and sends nothing cannot hold the listener

            def log_message(self, *args):  # nothing about a request is logged
                pass

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
