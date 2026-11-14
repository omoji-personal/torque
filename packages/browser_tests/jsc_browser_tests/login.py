"""`torque browser login`: sign a browser in to an org the Salesforce CLI is already
authorized for, through the Salesforce front door, with no password and without the
sign-in address ever reaching the session.

Host-tab mode (the default) is for an agent that drives a browser tab with its host's
browser tool. The command prints a local address; the tab opens it; the relay asks the
CLI for the single-use Salesforce address at that moment and redirects the tab to it.

The Salesforce address is valid once and for about a minute. It is never printed,
logged, written to disk or put in an exception: not here and not in relay.py.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import auth, relay
from .diagnostics import redact
from .sf_client import SfClient, SfError

EXIT_OK = 0
EXIT_TIMEOUT = 1          # nobody opened the local address in time
EXIT_USAGE = 2            # options that do not fit together (argparse's own code)
EXIT_NOT_AUTHORIZED = 3   # the alias has no CLI authorization, or it expired
EXIT_CLI_MISSING = 4      # the Salesforce CLI is not installed
EXIT_ORG_MISMATCH = 5     # the alias is not the org the client record (or the other alias) names
EXIT_POLICY = 6           # this workspace's mode does not allow it
EXIT_BIND = 7             # no local port could be opened
EXIT_PLAYWRIGHT = 8       # Playwright or its browser is not installed (--storage-state)
EXIT_SIGN_IN = 9          # the sign-in itself failed

EXIT_CODES_HELP = (
    "exit codes: 0 signed in (the redirect was sent, or the session file was written); 1 nobody opened "
    "the address in time; 2 options; 3 the alias is not authorized in the Salesforce CLI or its "
    "authorization expired; 4 the Salesforce CLI is not installed; 5 org mismatch; 6 refused by this "
    "workspace's mode; 7 no local port; 8 Playwright or its browser is missing; 9 the sign-in failed.")
API_ONLY_NOTE = ("A user with the API Only User permission cannot be signed in to a browser; "
                 "use a separate user for browser tests.")
NOT_CHECKED = "org not checked against the client record"
# One word that the CLI cannot read as an option. It is passed as one argument, never through a shell.
_ALIAS = re.compile(r"[^\s-]\S*")
_ORG_ID = re.compile(r"00D[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?")


class LoginError(Exception):
    """A refusal or failure with its exit code and a one-line reason that is safe to print."""

    def __init__(self, code: int, reason: str):
        super().__init__(reason)
        self.code, self.reason = code, reason


@dataclass
class Output:
    """Human lines, or one JSON object per line with --json. Results go to stdout and
    hints to stderr, so the first stdout line of host-tab mode is the local address alone."""
    as_json: bool
    out: object = None
    err: object = None

    def result(self, text: str, **event) -> None:
        stream = self.out or sys.stdout
        print(json.dumps(event) if self.as_json else text, file=stream, flush=True)

    def hint(self, text: str) -> None:
        if not self.as_json:
            print(text, file=self.err or sys.stderr, flush=True)

    def fail(self, error: LoginError) -> int:
        if self.as_json:
            print(json.dumps({"event": "failed", "exit_code": error.code, "reason": error.reason}),
                  file=self.out or sys.stdout, flush=True)
        else:
            print(f"error: {error.reason}", file=self.err or sys.stderr, flush=True)
        return error.code


def same_org(first: str, second: str) -> bool:
    return bool(first and second and first[:15] == second[:15])


def from_sf_error(error: SfError, alias: str, doing: str) -> LoginError:
    """The exit code and next step for a failed CLI call. The CLI's own text is used only
    in its redacted, shortened form (SfError), and not at all for the known causes."""
    kind = getattr(error, "kind", "other")
    if kind == "cli_missing":
        return LoginError(EXIT_CLI_MISSING, "the Salesforce CLI (sf) is not installed or not on PATH. "
                                            "Install it, authorize the org, then run this again.")
    if kind == "not_authorized":
        return LoginError(EXIT_NOT_AUTHORIZED,
                          f"the Salesforce CLI has no usable authorization for {alias} (none, or it expired). "
                          f"The consultant runs `sf org login web --alias {alias}` in their own terminal, "
                          "outside the agent; then run this again.")
    if kind == "api_only":
        return LoginError(EXIT_SIGN_IN, f"Salesforce refused a browser session for {alias}'s user. " + API_ONLY_NOTE)
    return LoginError(EXIT_SIGN_IN, f"{doing} failed for {alias}: {redact(str(error))[:300]}")


def policy_refusal() -> str | None:
    """Why this workspace's mode does not allow the command, or None. Connected mode
    allows one browser, Torque's own guarded one, and refuses host browser tools
    (torque.browser_guard, the gate): a sign-in for a host tab, or a saved session
    for another browser, would step outside that guard. Nothing is added here that
    the existing policy does not already say."""
    if not auth.in_connected_mode():
        return None
    return ("connected mode: this workspace allows only Torque's own guarded browser, which checks every "
            "request against the granted browser window, and refuses host browser tabs and saved sessions "
            "because their org cannot be checked. Run the journey with `torque browser browser FLOW "
            "--target-org ALIAS --workspace W --client NAME` under a granted browser window instead.")


def client_org_records(alias: str) -> list[tuple[str, str]]:
    """(source, org ID) for every place the selected client's own records name an org ID
    for this alias: the consent record and the test-user seed. Nothing is written and
    no new record is invented; a client that records only an alias gives an empty list."""
    try:
        from jsc_common.workspace import workspace_root
        client = workspace_root()
    except (ImportError, ValueError):
        return []
    found: list[tuple[str, str]] = []

    def read(path: Path):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    consent = read(client / "consent.json")
    for org in (consent or {}).get("approved_orgs") or []:
        if isinstance(org, dict) and org.get("alias") == alias and isinstance(org.get("org_id_18"), str):
            found.append(("consent record", org["org_id_18"]))
    seed_path = Path(os.environ["TORQUE_TEST_USERS"]) if os.environ.get("TORQUE_TEST_USERS") \
        else client / "config" / "test-users.json"
    seed = read(seed_path)
    if seed and seed.get("alias") == alias and isinstance(seed.get("org_id_18"), str):
        found.append(("test-user seed", seed["org_id_18"]))
    return [(source, org_id) for source, org_id in found if _ORG_ID.fullmatch(org_id)]


def check_against_client_record(alias: str, org_id: str) -> str:
    """Compare the org the alias resolves to with what the client's records say. Returns
    the sentence for the output; raises LoginError when a record names another org."""
    records = client_org_records(alias)
    for source, recorded in records:
        if not same_org(recorded, org_id):
            raise LoginError(EXIT_ORG_MISMATCH,
                             f"{alias} points to org {org_id}, but this client's {source} records {recorded} for "
                             "that alias. Nothing was signed in. Check which org the alias is authorized for "
                             "(`sf data query --query \"SELECT Id, IsSandbox FROM Organization\" --target-org "
                             f"{alias}`) or correct the record.")
    if records:
        return "org matches this client's " + " and ".join(dict.fromkeys(source for source, _ in records))
    return NOT_CHECKED


def identify(client: SfClient, alias: str) -> dict:
    """{"org_id", "is_sandbox", ...} for the alias through the Organization query."""
    try:
        return client.org_identity()
    except SfError as error:
        raise from_sf_error(error, alias, "the org identity query") from None


def org_kind(identity: dict) -> str:
    return {True: "sandbox", False: "production"}.get(identity.get("is_sandbox"), "unknown")


def minter(client: SfClient, alias: str, identity: dict, path: str | None, api_version: str | None):
    """The relay's `mint`: ask the CLI for the address now, and refuse the redirect when
    the answer is for another org than the one checked when the command started."""
    def mint() -> relay.Minted:
        try:
            answer = client.org_frontdoor(path=path, api_version=api_version)
        except SfError as error:
            failure = from_sf_error(error, alias, "the sign-in request")
            raise relay.RelayRefused(str(failure.code), failure.reason) from None
        if not same_org(answer.get("org_id") or "", identity["org_id"]):
            raise relay.RelayRefused(str(EXIT_ORG_MISMATCH),
                                     f"{alias} no longer points to org {identity['org_id']}; nothing was signed in")
        return relay.Minted(answer.get("url") or "", answer.get("org_id") or "", answer.get("username") or "")
    return mint


def user_id_of(client: SfClient, username: str) -> str | None:
    try:
        found = client.user_by_username(username) if username else None
    except SfError:
        return None
    return found["user_id"] if found else None


def _checked_options(args) -> None:
    if not _ALIAS.fullmatch(args.target_org or ""):
        raise LoginError(EXIT_USAGE, "--target-org must be one org alias or username, without spaces")
    path = args.path
    if path is not None and (not path.startswith("/") or path.startswith("//") or "\\" in path
                             or any(ord(char) < 32 for char in path) or len(path) > 2000):
        raise LoginError(EXIT_USAGE, "--path must be a path inside the org that starts with one /, "
                                     "for example /lightning/page/home")
    if not 1 <= args.timeout <= 900:
        raise LoginError(EXIT_USAGE, "--timeout must be between 1 and 900 seconds")
    if args.api_version is not None and not re.fullmatch(r"\d{2,3}\.0", args.api_version):
        raise LoginError(EXIT_USAGE, "--api-version looks like 62.0")


def run_host_tab(args, output: Output) -> int:
    alias = args.target_org
    client = SfClient(alias)
    identity = identify(client, alias)
    org_check = check_against_client_record(alias, identity["org_id"])
    try:
        listener = relay.Relay()
    except OSError as error:
        raise LoginError(EXIT_BIND, f"no local port could be opened on 127.0.0.1 ({type(error).__name__}). "
                                    "Close programs that hold many local ports, then run this again.") from None
    expires = time.time() + args.timeout
    output.result(listener.address, event="waiting", address=listener.address, timeout_seconds=args.timeout,
                  expires_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires)))
    output.hint(f"Open the address above in the browser tab within {args.timeout} seconds. It works once, on this "
                "machine only. Navigate the tab to it; do not fetch it with curl or a web fetch tool.")
    outcome = listener.serve(minter(client, alias, identity, args.path, args.api_version), timeout=args.timeout,
                             navigation_only=not args.any_client)
    if outcome.status == "timeout":
        raise LoginError(EXIT_TIMEOUT, outcome.reason + "; the listener closed and nothing was signed in")
    if outcome.status != "redirected":
        code = int(outcome.status) if outcome.status.isdigit() else EXIT_SIGN_IN
        raise LoginError(code, outcome.reason)
    username = outcome.minted.username
    user_id = user_id_of(client, username)
    kind = org_kind(identity)
    output.result(f"Redirect sent: org {identity['org_id']} ({kind}), user {username or 'unknown'}"
                  f"{', User Id ' + user_id if user_id else ''}; {org_check}.",
                  event="redirected", org_id=identity["org_id"], org_kind=kind, username=username or None,
                  user_id=user_id, org_check=org_check)
    output.hint("Wait until the tab shows the org (an action sent while it still redirects can fail), then confirm "
                "the signed-in user in the page before testing. A login page means the sign-in failed: retry once, "
                "then stop and report; never type or ask for a password.")
    return EXIT_OK


def cmd_login(args, out=None, err=None) -> int:
    output = Output(bool(getattr(args, "json", False)), out, err)
    try:
        _checked_options(args)
        refusal = policy_refusal()
        if refusal:
            raise LoginError(EXIT_POLICY, refusal)
        return run_host_tab(args, output)
    except LoginError as error:
        return output.fail(error)
    except Exception as error:  # no traceback: only the error's type is safe to show
        return output.fail(LoginError(EXIT_SIGN_IN, f"unexpected failure ({type(error).__name__}); nothing was "
                                                    "signed in"))


def add_parsers(sub) -> None:
    """Add `login` to the browser command's subparsers."""
    login = sub.add_parser(
        "login", help="sign a browser tab in through the Salesforce front door; prints only a local address",
        description="Sign a browser in to an org the Salesforce CLI is already authorized for. No password is "
                    "used and the Salesforce sign-in address is never printed. By default the command prints a "
                    "local address and waits: navigate the browser tab to it and the tab is redirected into the "
                    "org. " + API_ONLY_NOTE,
        epilog=EXIT_CODES_HELP)
    login.add_argument("--target-org", required=True, help="org alias the Salesforce CLI is authorized for")
    login.add_argument("--path", help="page to land on, for example /lightning/page/home (default: the org's own "
                                      "landing page; go to a record or page after the sign-in)")
    login.add_argument("--timeout", type=int, default=180, help="seconds to wait for the browser (default 180)")
    login.add_argument("--api-version", help="API version for the CLI call, for example 62.0, when its cached "
                                             "version is wrong after a sandbox refresh")
    login.add_argument("--any-client", action="store_true",
                       help="also answer a client that does not say it is a browser navigation (a browser "
                            "without fetch metadata); off by default so that a fetch tool cannot receive the address")
    login.add_argument("--json", action="store_true", help="one JSON object per line instead of text")
    login.set_defaults(func=cmd_login)
