"""`torque browser login`: sign a browser in to an org the Salesforce CLI is already
authorized for, through the Salesforce front door, with no password and without the
sign-in address ever reaching the session.

Host-tab mode (the default) is for an agent that drives a browser tab with its host's
browser tool. The command prints a local address; the tab opens it; the relay asks the
CLI for the single-use Salesforce address at that moment and redirects the tab to it.

Playwright mode (--storage-state PATH) is for a scripted browser. Torque's own
headless browser signs in through the package's existing path, the page's user is
checked, and the session is written to a file only its owner can read.
`torque browser logout --storage-state PATH` deletes that file.

The Salesforce address is valid once and for about a minute. It is never printed,
logged, written to disk or put in an exception: not here and not in relay.py.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import secrets
import subprocess
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
    "exit codes: 0 the tab was sent to the org's sign-in address (not yet confirmed: check the page), or the "
    "session file was written after the page's user was checked; 1 the address "
    "expired before a browser opened it (nothing was signed in); 2 options; 3 the alias is not authorized in the Salesforce CLI or its "
    "authorization expired; 4 the Salesforce CLI is not installed; 5 another org or user than recorded; 6 refused by this "
    "workspace's mode; 7 no local port; 8 Playwright or its browser is missing; 9 the sign-in failed.")
API_ONLY_NOTE = ("A user with the API Only User permission cannot be signed in to a browser; "
                 "use a separate user for browser tests.")
NOT_CHECKED = "org not checked against the client record"
CHECK_IN_PAGE = ("Next: wait until the tab shows the org's page (an action sent while it still redirects can fail), "
                 "then check which user the page shows before testing. A login page means the sign-in failed: retry "
                 "once, then stop and report; never type or ask for a password. A \"Change Your Password\" page means "
                 "that user's password has expired: only the consultant can change it (the CLI and API keep working). "
                 "A device verification page needs the consultant too.")
# One word that the CLI cannot read as an option. It is passed as one argument, never through a shell.
_ALIAS = re.compile(r"[^\s-]\S*")
_ORG_ID = re.compile(r"00D[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?")
# What the CLI's identity fields look like. A value of another shape is printed as UNREADABLE:
# the command prints what the CLI returned, and a field that does not look like what it should
# be is not repeated, so that it can never carry an address or a token into the output.
_USER_ID = re.compile(r"005[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?")
_USERNAME = re.compile(r"[A-Za-z0-9._'+-]{1,80}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){1,10}")
_EDITION = re.compile(r"[A-Za-z][A-Za-z ]{0,59}")
UNREADABLE = "(unreadable)"


def shown(value, shape: re.Pattern) -> str:
    """`value` when it has the shape it should have, "" when there is none, else UNREADABLE."""
    if value in (None, ""):
        return ""
    if isinstance(value, str) and shape.fullmatch(value) and redact(value) == value:
        return value
    return UNREADABLE


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

    # Everything printed passes the package's redaction as well: the loopback address the
    # command prints on purpose has no credential shape and comes through unchanged.

    def result(self, text: str, **event) -> None:
        stream = self.out or sys.stdout
        print(json.dumps(redact(event)) if self.as_json else redact(text), file=stream, flush=True)

    def hint(self, text: str) -> None:
        if not self.as_json:
            print(redact(text), file=self.err or sys.stderr, flush=True)

    def fail(self, error: LoginError) -> int:
        reason = redact(error.reason)
        if self.as_json:
            print(json.dumps({"event": "failed", "exit_code": error.code, "reason": reason}),
                  file=self.out or sys.stdout, flush=True)
        else:
            print(f"error: {reason}", file=self.err or sys.stderr, flush=True)
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
    if auth.in_build_only_mode():
        # The gate hook blocks the command here too; this covers a host with no hook wired.
        return ("build-only mode: this workspace keeps the AI session away from the org, and a browser sent into "
                "the org is org access. Nothing was signed in. Run the command yourself outside the AI session, "
                "or the owner sets the workspace's ai_access to full or connected.")
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
    client = _client_folder()
    if client is None:
        return []
    found: list[tuple[str, str]] = []
    consent = _read_json(client / "consent.json")
    for org in (consent or {}).get("approved_orgs") or []:
        if isinstance(org, dict) and org.get("alias") == alias and isinstance(org.get("org_id_18"), str):
            found.append(("consent record", org["org_id_18"]))
    seed = _seed_for(alias)
    if seed and isinstance(seed.get("org_id_18"), str):
        found.append(("test-user seed", seed["org_id_18"]))
    return [(source, org_id) for source, org_id in found if _ORG_ID.fullmatch(org_id)]


def _client_folder() -> Path | None:
    try:
        from jsc_common.workspace import workspace_root
        return workspace_root()
    except (ImportError, ValueError):
        return None


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _seed_for(alias: str) -> dict | None:
    """The selected client's test-user seed, when it is written for this org alias."""
    client = _client_folder()
    if os.environ.get("TORQUE_TEST_USERS"):
        seed = _read_json(Path(os.environ["TORQUE_TEST_USERS"]))
    else:
        seed = _read_json(client / "config" / "test-users.json") if client else None
    return seed if seed and seed.get("alias") == alias else None


def seed_role(alias: str, name: str) -> dict | None:
    """The seed's entry for a role that names an alias of its own (`cli_alias`): the
    alias to sign in with and the user the seed expects it to be. None for a name that
    is not such a role, which is then taken as an alias itself."""
    users = (_seed_for(alias) or {}).get("users")
    entry = users.get(name) if isinstance(users, dict) else None
    if not isinstance(entry, dict) or not isinstance(entry.get("cli_alias"), str) \
            or not _ALIAS.fullmatch(entry["cli_alias"]):
        return None
    return {"cli_alias": entry["cli_alias"], "role": name,
            "username": entry.get("username") if isinstance(entry.get("username"), str) else ""}


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


def org_edition(identity: dict) -> str:
    """The org's edition as the Organization query names it, or "". A Developer Edition org is
    "production" by the platform's sandbox flag; printing the edition beside it says what it is."""
    edition = identity.get("organization_type")
    return shown(edition.strip(), _EDITION) if isinstance(edition, str) else ""


def wrong_user(alias: str, username: str, expected: str) -> str | None:
    """Why the alias's user is not the user the seed's role records, or None."""
    if not expected or username.casefold() == expected.casefold():
        return None
    return (f"{alias} signs in as {shown(username, _USERNAME) or 'an unknown user'}, but the test-user seed records "
            f"{shown(expected, _USERNAME)} for that role; nothing was signed in")


def minter(client: SfClient, alias: str, identity: dict, path: str | None, api_version: str | None,
           expect_username: str = ""):
    """The relay's `mint`: ask the CLI for the address now, and refuse the redirect when
    the answer is for another org than the one checked when the command started, or for
    another user than the seed's role records."""
    def mint() -> relay.Minted:
        try:
            answer = client.org_frontdoor(path=path, api_version=api_version)
        except SfError as error:
            failure = from_sf_error(error, alias, "the sign-in request")
            raise relay.RelayRefused(str(failure.code), failure.reason) from None
        if not same_org(answer.get("org_id") or "", identity["org_id"]):
            raise relay.RelayRefused(str(EXIT_ORG_MISMATCH),
                                     f"{alias} no longer points to org {identity['org_id']}; nothing was signed in")
        other = wrong_user(alias, answer.get("username") or "", expect_username)
        if other:
            raise relay.RelayRefused(str(EXIT_ORG_MISMATCH), other)
        return relay.Minted(answer.get("url") or "", answer.get("org_id") or "", answer.get("username") or "")
    return mint


def user_id_of(client: SfClient, username: str) -> str | None:
    """The User Id for a username the CLI returned, or None. A username that does not
    look like one is not looked up."""
    if shown(username, _USERNAME) in ("", UNREADABLE):
        return None
    try:
        found = client.user_by_username(username)
    except SfError:
        return None
    return shown(found["user_id"], _USER_ID) if found else None




def _checked_options(args) -> None:
    for option, value in (("--target-org", args.target_org), ("--as-alias", args.as_alias)):
        if value is not None and not _ALIAS.fullmatch(value):
            raise LoginError(EXIT_USAGE, f"{option} must be one org alias or username, without spaces")
    path = args.path
    if path is not None and (not path.startswith("/") or path.startswith("//") or "\\" in path
                             or any(ord(char) < 32 for char in path) or len(path) > 2000):
        raise LoginError(EXIT_USAGE, "--path must be a path inside the org that starts with one /, "
                                     "for example /lightning/page/home")
    if not 1 <= args.timeout <= 900:
        raise LoginError(EXIT_USAGE, "--timeout must be between 1 and 900 seconds")
    if args.api_version is not None and not re.fullmatch(r"\d{2,3}\.0", args.api_version):
        raise LoginError(EXIT_USAGE, "--api-version looks like 62.0")
    if args.as_alias and args.as_user:
        raise LoginError(EXIT_USAGE, "use --as-alias or --as-user, not both")
    if args.as_user is not None and (not args.as_user.strip() or any(ord(char) < 32 for char in args.as_user)):
        raise LoginError(EXIT_USAGE, "--as-user takes a username or a User Id")
    if args.direct and not args.as_user:
        raise LoginError(EXIT_USAGE, "--direct belongs to --as-user")
    if args.as_user and args.path:
        raise LoginError(EXIT_USAGE, "--as-user lands on that user's page in Setup; leave --path out")
    if args.as_user and args.storage_state:
        raise LoginError(EXIT_USAGE, "--storage-state signs in as an alias's own user: authorize the test user "
                                     "in the Salesforce CLI and use --as-alias instead of --as-user")


@dataclass
class Plan:
    """What the command will do, settled before anything is printed or signed in."""
    target_alias: str
    alias: str                # the alias the session is asked for: --target-org, or --as-alias
    client: SfClient          # the CLI client for `alias`
    identity: dict            # --target-org's org, from the Organization query
    org_check: str
    path: str | None
    login_as: dict | None = None   # --as-user: {"user_id", "username", "route"}
    role: str = ""                 # --as-alias named a role of the test-user seed
    expect_username: str = ""      # the user that role's seed entry records


def prepare(args) -> Plan:
    target = args.target_org
    admin = SfClient(target)
    identity = identify(admin, target)
    org_check = check_against_client_record(target, identity["org_id"])
    plan = Plan(target, target, admin, identity, org_check, args.path)
    if args.as_alias:
        # A role of the client's test-user seed that names its own alias (cli_alias), or an alias itself.
        role = seed_role(target, args.as_alias)
        user_alias = role["cli_alias"] if role else args.as_alias
        if role:
            plan.role, plan.expect_username = role["role"], role["username"]
        user_client = SfClient(user_alias)
        other = identify(user_client, user_alias)
        if not same_org(other["org_id"], identity["org_id"]):
            raise LoginError(EXIT_ORG_MISMATCH,
                             f"--as-alias {user_alias} is authorized for org {shown(other['org_id'], _ORG_ID)}, but "
                             f"--target-org {target} is org {shown(identity['org_id'], _ORG_ID)}. Nothing was signed "
                             "in. Authorize the test user "
                             "in the same org first (the consultant runs `sf org login web --alias "
                             f"{user_alias}` against that org, outside the agent).")
        plan.alias, plan.client = user_alias, user_client
    if args.as_user:
        try:
            found = admin.find_user(args.as_user.strip())
        except SfError as error:
            raise from_sf_error(error, target, "the user lookup") from None
        if not found:
            raise LoginError(EXIT_SIGN_IN, f"no user {args.as_user!r} in org {identity['org_id']}; give the full "
                                           "username or the User Id")
        if not _USER_ID.fullmatch(found["user_id"]):
            raise LoginError(EXIT_SIGN_IN, f"the user lookup for {args.as_user!r} returned an unreadable User Id; "
                                           "nothing was signed in. Give the User Id itself")
        username = shown(found["username"], _USERNAME)
        if not found["is_active"]:
            raise LoginError(EXIT_SIGN_IN, f"user {username or found['user_id']} is inactive; an inactive "
                                           "user cannot be logged in as")
        route = "direct" if args.direct else "setup-page"
        plan.login_as = {"user_id": found["user_id"], "username": username, "route": route}
        plan.path = (auth.login_as_path(identity["org_id"], found["user_id"]) if args.direct
                     else auth.user_setup_path(found["user_id"]))
    return plan


def login_as_steps(plan: Plan) -> list[str]:
    """Plain next steps for the agent after the admin's tab was redirected (--as-user)."""
    user = plan.login_as["username"] or plan.login_as["user_id"]
    setup = auth.user_setup_path(plan.login_as["user_id"])
    needs = ("Log in as needs the org's \"Administrators Can Log in as Any User\" policy (Setup, Login Access "
             "Policies) or login access granted by the user, and Modify All Data for the signed-in admin. "
             + API_ONLY_NOTE)
    done = ("When done, do not click \"Log out as\" (it can end the whole session): close the tab, or run "
            "torque browser login again to be the admin.")
    if plan.login_as["route"] == "direct":
        return [f"The tab was sent straight to Salesforce's Login As address for {user}. This route is unproven "
                "until a live run confirms it.",
                f"Confirm the \"Logged in as {user}\" banner at the top of the page. Without it the tab is still "
                f"the admin: go to {setup} in the org and click Login there.",
                done, needs]
    return [f"The tab was sent, as the admin, to the page of {user} in Setup. If it shows another page, "
            f"navigate to {setup} in the org.",
            "Click Login on that page.",
            f"Confirm the \"Logged in as {user}\" banner at the top of the page before testing. Without it the tab "
            "is still the admin and every check would pass as the admin.",
            done, needs]


def run_host_tab(args, plan: Plan, output: Output) -> int:
    try:
        listener = relay.Relay()
    except OSError as error:
        raise LoginError(EXIT_BIND, f"no local port could be opened on 127.0.0.1 ({type(error).__name__}). "
                                    "Close programs that hold many local ports, then run this again.") from None
    # One deadline: the printed expiry is the moment the relay stops asking for, and sending, an address.
    deadline, expires = time.monotonic() + args.timeout, time.time() + args.timeout
    output.result(listener.address, event="waiting", address=listener.address, timeout_seconds=args.timeout,
                  expires_at=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(expires))
                  + f".{int(expires % 1 * 1000):03d}Z")
    output.hint(f"Open the address above in the browser tab within {args.timeout} seconds. It works once, on this "
                "machine only. Navigate the tab to it; do not fetch it with curl or a web fetch tool.")
    outcome = listener.serve(minter(plan.client, plan.alias, plan.identity, plan.path, args.api_version,
                                    plan.expect_username),
                             timeout=args.timeout, deadline=deadline, navigation_only=not args.any_client)
    if outcome.status == "timeout":
        raise LoginError(EXIT_TIMEOUT, outcome.reason + "; the listener closed"
                         + ("" if "nothing was signed in" in outcome.reason else " and nothing was signed in"))
    if outcome.status != "redirected":
        code = int(outcome.status) if outcome.status.isdigit() else EXIT_SIGN_IN
        raise LoginError(code, outcome.reason)
    user_id = user_id_of(plan.client, outcome.minted.username)
    username = shown(outcome.minted.username, _USERNAME)
    org_id = shown(plan.identity["org_id"], _ORG_ID)
    kind = org_kind(plan.identity)
    edition = org_edition(plan.identity)
    # All the command knows: the tab was sent to the org's sign-in address as this user. It
    # cannot see the tab, so nothing here says "signed in".
    event = {"event": "sent", "session_verified": False, "org_id": org_id, "org_kind": kind,
             "org_edition": edition or None,
             "username": username or None, "user_id": user_id, "org_check": plan.org_check,
             "check_in_page": CHECK_IN_PAGE}
    text = (f"Tab sent to the org's sign-in address: org {org_id} ({kind}{', ' + edition if edition else ''}), "
            f"user {username or 'unknown'}"
            f"{', User Id ' + user_id if user_id else ''}; {plan.org_check}. Not confirmed: the command cannot see "
            "the tab.")
    if plan.alias != plan.target_alias:
        event["as_alias"] = plan.alias
        text += f" Sent through alias {plan.alias}."
    if plan.role:
        event["role"] = plan.role
        text += f" Role {plan.role} of the test-user seed."
    if plan.login_as:
        steps = login_as_steps(plan)
        event.update(login_as=plan.login_as, next_steps=steps)
        text += "\nNext steps:\n" + "\n".join(f"{number}. {step}" for number, step in enumerate(steps, 1))
    output.result(text, **event)
    output.hint(CHECK_IN_PAGE)
    return EXIT_OK


# Playwright mode: a session file for a scripted browser (--storage-state).

PLAYWRIGHT_MISSING = ("Playwright mode needs Torque's browser extra and a Playwright browser: "
                      "`python -m pip install \"torque-salesforce[browser]\"`, then `python -m playwright install "
                      "chromium`. Signing a host browser tab in (without --storage-state) needs neither.")


def workspace_folder_problem(path: Path) -> str | None:
    """Why `path` lies in a Torque workspace folder that is not the selected client's:
    another client's folder (clients/OTHER/...) or an initiative's (initiatives/NAME/...).
    A session file signs a browser in to this org, so it belongs with this client only."""
    selected = _client_folder()
    for parent in path.parents:
        root = parent.parent
        if parent.name not in ("clients", "initiatives") or root == parent \
                or not ((root / "workspace.json").is_file() or (root / ".torque").is_dir()):
            continue
        owner = path.relative_to(parent).parts[0]
        if parent.name == "clients" and selected is not None and selected == (parent / owner).resolve():
            return None
        whose = "an initiative's folder" if parent.name == "initiatives" else (
            "a client folder, and no client is selected (pass --client)" if selected is None
            else "another client's folder")
        return (f"{path} is inside {whose} of the workspace ({parent.name}/{owner}). A session file signs a browser "
                "in to this org, so it belongs in the selected client's folder, for example "
                "clients/NAME/state/browser-auth/ALIAS.json, or outside any repository.")
    return None


def storage_path_problem(path: Path) -> str | None:
    """Why a session file may not be written at `path`, or None. The file signs a browser
    in as the user, so it must stay with the selected client and must not be able to reach
    a repository: a path in another client's or an initiative's folder of the workspace is
    refused, and a path inside a git work tree is refused unless git ignores it."""
    elsewhere = workspace_folder_problem(path)
    if elsewhere:
        return elsewhere
    folder = next((parent for parent in path.parents if parent.is_dir()), None)
    if folder is None or not any((parent / ".git").exists() for parent in [folder, *folder.parents]):
        return None
    advice = ("Choose a path that git ignores (a Torque workspace ignores its clients/ folder, for example "
              "clients/NAME/state/browser-auth/ALIAS.json) or one outside any repository.")
    try:
        done = subprocess.run(["git", "-C", str(folder), "check-ignore", "-q", "--", str(path)],
                              capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return f"{path} is inside a git work tree and git could not say whether it is ignored. " + advice
    if done.returncode == 0:
        return None
    return (f"{path} is inside a git work tree and is not ignored, so the session file could be committed. "
            + advice)


def write_private(path: Path, text: str) -> None:
    """Write a file only its owner can read: created private from its first byte under a
    temporary name, then moved into place."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    if os.name == "nt":
        from jsc_common import winacl
        descriptor = winacl.create_private(temporary)
    else:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


async def capture_state(plan: Plan, api_version: str | None) -> tuple[dict, str, str]:
    """Sign Torque's own headless browser in through the package's existing path
    (auth.open_session, with its guard), confirm the page's user is the alias's user,
    and return (storage state, User Id, username). The address is asked for only after
    the browser is known to exist, and is navigated to at once."""
    from playwright.async_api import async_playwright
    from .library.lightning_page import LIGHTNING_SHELL_SELECTORS
    async with async_playwright() as playwright:
        executable = getattr(playwright.chromium, "executable_path", "") or ""
        if not os.path.exists(executable):
            raise LoginError(EXIT_PLAYWRIGHT, "no Playwright browser is installed. " + PLAYWRIGHT_MISSING)
        try:
            admin = auth.get_admin_auth(plan.client, plan.alias, path=plan.path, api_version=api_version)
        except SfError as error:
            raise from_sf_error(error, plan.alias, "the sign-in request") from None
        if not same_org(admin.org_id_18, plan.identity["org_id"]):
            raise LoginError(EXIT_ORG_MISMATCH, f"{plan.alias} no longer points to org {plan.identity['org_id']}; "
                                                "nothing was signed in")
        other = wrong_user(plan.alias, admin.username, plan.expect_username)
        if other:
            raise LoginError(EXIT_ORG_MISMATCH, other)
        session = None
        try:
            session = await auth.open_session(playwright, admin, cdp_endpoint=None, headed=False)
            page = session.page
            await page.wait_for_selector(LIGHTNING_SHELL_SELECTORS, state="visible", timeout=30000)
            user_id = await auth.observe_user_id(page)
            username = await auth.observe_username(page, user_id, api_version)
            if not admin.username or username.casefold() != admin.username.casefold():
                raise LoginError(EXIT_SIGN_IN, "the browser's signed-in user is not the alias's user; no session "
                                               "file was written")
            return await session.context.storage_state(), user_id, username
        except LoginError:
            raise
        except auth.AuthError as error:
            raise LoginError(EXIT_SIGN_IN, f"the sign-in could not be confirmed: {redact(str(error))[:300]}") from None
        except Exception as error:  # Playwright errors repeat the navigated address: keep the type only
            raise LoginError(EXIT_SIGN_IN, f"the sign-in could not be confirmed ({type(error).__name__}); no session "
                                           "file was written") from None
        finally:
            await auth.close_session(session)


def run_storage_state(args, output: Output) -> int:
    path = Path(args.storage_state).expanduser().resolve()
    if path.is_dir():
        raise LoginError(EXIT_USAGE, f"--storage-state names a folder: {path}")
    problem = storage_path_problem(path)
    if problem:
        raise LoginError(EXIT_USAGE, problem)
    debug = auth.debug_env_problem()
    if debug:
        raise LoginError(EXIT_USAGE, debug)
    if importlib.util.find_spec("playwright") is None:
        raise LoginError(EXIT_PLAYWRIGHT, "Playwright is not installed. " + PLAYWRIGHT_MISSING)
    plan = prepare(args)
    state, user_id, username = asyncio.run(capture_state(plan, args.api_version))
    write_private(path, json.dumps(state))
    kind = org_kind(plan.identity)
    edition = org_edition(plan.identity)
    username, user_id = shown(username, _USERNAME), shown(user_id, _USER_ID)
    event = {"event": "saved", "session_verified": True, "storage_state": str(path),
             "org_id": plan.identity["org_id"], "org_kind": kind, "org_edition": edition or None,
             "username": username, "user_id": user_id, "org_check": plan.org_check}
    if plan.alias != plan.target_alias:
        event["as_alias"] = plan.alias
    output.result(f"Session file written: {path}\norg {plan.identity['org_id']} ({kind}"
                  f"{', ' + edition if edition else ''}), user {username}, User Id {user_id}; {plan.org_check}.",
                  **event)
    output.hint("The file signs a browser in as that user until the session ends. Keep it out of repositories and "
                f"delete it when done: torque browser logout --storage-state {path}")
    return EXIT_OK


def cmd_login(args, out=None, err=None) -> int:
    output = Output(bool(getattr(args, "json", False)), out, err)
    try:
        _checked_options(args)
        refusal = policy_refusal()
        if refusal:
            raise LoginError(EXIT_POLICY, refusal)
        if args.storage_state:
            return run_storage_state(args, output)
        return run_host_tab(args, prepare(args), output)
    except LoginError as error:
        return output.fail(error)
    except Exception as error:  # no traceback: only the error's type is safe to show
        return output.fail(LoginError(EXIT_SIGN_IN, f"unexpected failure ({type(error).__name__}); nothing was "
                                                    "signed in"))


def cmd_logout(args, out=None, err=None) -> int:
    """Delete a session file that `login --storage-state` wrote. Nothing else is deleted:
    a file that is not a browser session file is left alone."""
    output = Output(bool(getattr(args, "json", False)), out, err)
    path = Path(args.storage_state).expanduser().resolve()
    try:
        if not path.exists():
            output.result(f"No session file at {path}; nothing to delete.", event="absent", storage_state=str(path))
            return EXIT_OK
        try:
            state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        except (OSError, ValueError):
            state = None
        if not (isinstance(state, dict) and isinstance(state.get("cookies"), list)
                and isinstance(state.get("origins"), list)):
            raise LoginError(EXIT_USAGE, f"{path} is not a browser session file written by torque browser login; "
                                         "nothing was deleted")
        path.unlink()
        output.result(f"Session file deleted: {path}", event="deleted", storage_state=str(path))
        output.hint("The Salesforce session itself ends at the org's own timeout; no browser can reuse it from "
                    "this file any more.")
        return EXIT_OK
    except LoginError as error:
        return output.fail(error)
    except OSError as error:
        return output.fail(LoginError(EXIT_SIGN_IN, f"the session file could not be deleted ({type(error).__name__})"))


def add_parsers(sub) -> None:
    """Add `login` and `logout` to the browser command's subparsers."""
    login = sub.add_parser(
        "login", help="send a browser to the org through the Salesforce front door; prints only a local address",
        description="Send a browser to an org the Salesforce CLI is already authorized for, through its "
                    "single-use sign-in address. No password is used and that address is never printed. By "
                    "default the command prints a local address and waits: navigate the browser tab to it and the "
                    "tab is redirected to the org's sign-in address. The command cannot see the tab: check the "
                    "page and its user afterwards. With --storage-state it signs Torque's own headless browser "
                    "in, checks the page's user, and writes a session file for a scripted browser instead. "
                    + API_ONLY_NOTE,
        epilog=EXIT_CODES_HELP)
    login.add_argument("--target-org", required=True, help="org alias the Salesforce CLI is authorized for")
    login.add_argument("--path", help="page to land on, for example /lightning/page/home (default: the org's own "
                                      "landing page; go to a record or page after the sign-in)")
    login.add_argument("--timeout", type=int, default=180, help="seconds to wait for the browser (default 180)")
    login.add_argument("--api-version", help="API version for the CLI call, for example 62.0, when its cached "
                                             "version is wrong after a sandbox refresh")
    login.add_argument("--as-alias", metavar="USER_ALIAS",
                       help="sign in as the user of this other alias, which the Salesforce CLI is separately "
                            "authorized for as the test user; it must be the same org as --target-org. A role "
                            "name from the client's test-user seed that has a cli_alias works too, and then the "
                            "user is checked against the seed")
    login.add_argument("--as-user", metavar="USERNAME_OR_ID",
                       help="open that user's page in Setup in the admin's tab, where the Login button is, and "
                            "print the next steps (needs the org's Log in as policy; not for API-only or "
                            "inactive users)")
    login.add_argument("--direct", action="store_true",
                       help="with --as-user: send the tab straight to Salesforce's Login As address instead of "
                            "the Setup page; unproven until a live run confirms it")
    login.add_argument("--storage-state", metavar="PATH",
                       help="Playwright mode: sign Torque's own headless browser in, check the org and user, and "
                            "write a session file only its owner can read; the path is refused inside a git "
                            "work tree unless git ignores it")
    login.add_argument("--any-client", action="store_true",
                       help="also answer a client that does not say it is a browser navigation (a browser "
                            "without fetch metadata); off by default so that a fetch tool cannot receive the address")
    login.add_argument("--json", action="store_true", help="one JSON object per line instead of text")
    login.set_defaults(func=cmd_login)

    logout = sub.add_parser("logout", help="delete a session file that login --storage-state wrote",
                            description="Delete a browser session file written by `login --storage-state`. A file "
                                        "that is not such a session file is left alone.")
    logout.add_argument("--storage-state", required=True, metavar="PATH")
    logout.add_argument("--json", action="store_true", help="one JSON object instead of text")
    logout.set_defaults(func=cmd_logout)
