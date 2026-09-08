"""The only Salesforce execution boundary used by :mod:`jsc_advisory`.

This module makes the package's read-only contract executable. Even if a future
caller passes the wrong argv, ``SfClient`` refuses every Salesforce CLI shape
except ``sf data query`` and ``sf sobject describe`` before spawning a process.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from typing import Sequence


class AdvisorySafetyError(RuntimeError):
    """A caller attempted to leave the package's read-only command surface."""


class Unknown(RuntimeError):
    """A Salesforce fact could not be established; it is never an empty result."""


_API_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
def require_api_name(value: str, label: str = "API name") -> str:
    """Return a syntactically safe Salesforce API identifier or raise ValueError."""
    if not value or not _API_NAME.fullmatch(value):
        raise ValueError(f"invalid {label}: {value!r}")
    return value


def soql_literal(value: str) -> str:
    """Escape an operator-supplied string for a single-quoted SOQL literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def first_reason(stderr: str, stdout: str = "") -> str:
    """Extract a useful, single-line failure reason from CLI output."""
    for raw in (stderr + "\n" + stdout).splitlines():
        line = re.sub(r"\x1b\[[0-9;]*m", "", raw).strip(" ›\t")
        if not line or re.match(r"^(warning|info|note)\b", line, re.I):
            continue
        if re.search(r"\berror\b|not found|invalid|expired|no org config", line, re.I):
            return line[:220]
    for raw in (stderr + "\n" + stdout).splitlines():
        line = raw.strip()
        if line:
            return line[:220]
    return "Salesforce CLI failed without a readable reason"


@dataclass(frozen=True)
class Invocation:
    argv: tuple[str, ...]
    timeout: int


class SfClient:
    """Small JSON-mode client whose public methods are both read-only."""

    def __init__(self) -> None:
        self.invocations: list[Invocation] = []

    @staticmethod
    def _assert_read_only(args: Sequence[str]) -> None:
        """Accept only the two exact argv shapes constructed below.

        Checking only the first two words would let a future caller append a
        new CLI flag with local side effects. This package does not intercept or
        restrict JSC's ordinary shell commands; it narrowly constrains its own
        subprocess boundary.
        """
        values = tuple(args)
        query_shape = (
            len(values) in (7, 8)
            and values[:3] == ("data", "query", "--target-org")
            and bool(values[3]) and not values[3].startswith("-")
            and values[4:6] == ("--json", "--query")
            and bool(values[6])
            and (len(values) == 7 or values[7] == "--use-tooling-api")
        )
        describe_shape = (
            len(values) == 7
            and values[:3] == ("sobject", "describe", "--target-org")
            and bool(values[3]) and not values[3].startswith("-")
            and values[4] == "--sobject"
            and bool(values[5]) and not values[5].startswith("-")
            and values[6] == "--json"
        )
        if not (query_shape or describe_shape):
            raise AdvisorySafetyError(
                "jsc-advisory may only execute its fixed `sf data query` and "
                f"`sf sobject describe` JSON argv shapes; refused: sf {' '.join(args)}"
            )

    def run(self, args: Sequence[str], timeout: int = 120) -> dict:
        self._assert_read_only(args)
        argv = ("sf", *tuple(args))
        self.invocations.append(Invocation(argv=argv, timeout=timeout))
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise Unknown(f"{type(exc).__name__}: {str(exc)[:140]}") from exc
        try:
            payload = json.loads(proc.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise Unknown(first_reason(proc.stderr, proc.stdout)) from exc
        if not isinstance(payload, dict):
            raise Unknown("Salesforce returned a non-object JSON envelope")
        if proc.returncode != 0 or payload.get("status") not in (0, None):
            raise Unknown(
                f"{payload.get('name', 'query failed')}: "
                f"{str(payload.get('message') or first_reason(proc.stderr, proc.stdout))[:180]}"
            )
        if "result" not in payload:
            raise Unknown("Salesforce returned success without a result")
        return payload["result"]

    def query(self, target_org: str, soql: str, *, tooling: bool = False,
              timeout: int = 120) -> dict:
        args = ["data", "query", "--target-org", target_org, "--json", "--query", soql]
        if tooling:
            args.append("--use-tooling-api")
        result = self.run(args, timeout=timeout)
        if not isinstance(result, dict):
            raise Unknown("Salesforce query result was not an object")
        return result

    def describe(self, target_org: str, sobject: str, *, timeout: int = 120) -> dict:
        require_api_name(sobject, "sObject API name")
        result = self.run(
            ["sobject", "describe", "--target-org", target_org,
             "--sobject", sobject, "--json"],
            timeout=timeout,
        )
        if not isinstance(result, dict):
            raise Unknown("Salesforce describe result was not an object")
        return result


def records(result: dict) -> list[dict]:
    rows = result.get("records")
    if not isinstance(rows, list):
        raise Unknown("Salesforce query result omitted its records list")
    return [row for row in rows if isinstance(row, dict)]


def total_size(result: dict) -> int:
    value = result.get("totalSize")
    if not isinstance(value, int) or isinstance(value, bool):
        raise Unknown("Salesforce query result omitted its numeric totalSize")
    return value
