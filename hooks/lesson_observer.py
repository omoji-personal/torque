#!/usr/bin/env python3
"""lesson_observer — notice when the platform refuses something, and again when it stops refusing.

WHY AUTOMATIC CAPTURE, AND WHY THIS SIGNAL

`torque lesson` is the right shape for knowledge — a fact the schema enforces, or a fixture that
runs forever — and the wrong shape for capture. Nobody types six flags at the moment they learn
something, because that moment is always inside an incident. Capture rate is the whole ballgame,
and a system with a good format and no intake is a system that stays empty.

So this observes instead of asking. It runs after a tool call and looks for exactly one thing: a
Salesforce operation that failed with a code from the platform's own error taxonomy. That is a
deliberately narrow signal, and the narrowness is the point —

  * it is structured, not judged. `INVALID_FIELD` is a fact about what happened, not an opinion
    about whether it was interesting.
  * it excludes the noise that kills auto-capture systems. A typo produces a shell error, not a
    platform error code. A wrong alias produces an auth error, which is not in the taxonomy.
  * it pairs. When a later command of the same shape SUCCEEDS against the same object, the two
    halves together say what actually fixed it — which is the part a note never records, because
    by then the person has moved on.

What it does NOT do is write knowledge. Candidates land in `local/` — gitignored, 0600, redacted
— and reach the catalogue only through `torque lesson`, where the schema and the live verifier
still apply. An observer that could write to the catalogue would be a machine for generating
confident nonsense.

The queue cannot rot quietly either: `lesson_backlog` in the harness reports its age, so an
ignored candidate becomes a visible WARN rather than a file nobody opens. That is the failure
mode this whole design exists to avoid, and it applies to this half too.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402

CANDIDATES = lib.TORQUE_HOME / "local" / "lessons" / "candidates.jsonl"

# Salesforce's own error taxonomy. Membership here is what makes a failure a signal rather than
# noise — every one of these names a platform behaviour, so every one is a candidate lesson.
SF_ERRORS = (
    "INVALID_FIELD", "INVALID_TYPE", "INVALID_CROSS_REFERENCE_KEY",
    "FIELD_CUSTOM_VALIDATION_EXCEPTION", "REQUIRED_FIELD_MISSING",
    "FIELD_INTEGRITY_EXCEPTION", "INSUFFICIENT_ACCESS_OR_READONLY",
    "INSUFFICIENT_ACCESS_ON_CROSS_REFERENCE_ENTITY", "DUPLICATE_VALUE",
    "UNABLE_TO_LOCK_ROW", "REQUEST_LIMIT_EXCEEDED", "STORAGE_LIMIT_EXCEEDED",
    "ENTITY_IS_DELETED", "MALFORMED_QUERY", "INVALID_SESSION_ID",
    "CANNOT_INSERT_UPDATE_ACTIVATE_ENTITY", "UNKNOWN_EXCEPTION",
    "MISSING_RECORD", "TXN_SECURITY_METERING_ERROR", "OPERATION_TOO_LARGE",
    "OP_WITH_INVALID_USER_TYPE_EXCEPTION", "OPERATION_ENQUEUED_ON_UPGRADE",
)
_ERR_RE = re.compile(r"\b(" + "|".join(SF_ERRORS) + r")\b")

# The code is only on the wire when `--json` was passed. Human-readable output carries the same
# failure as prose, and a consultant working interactively is not passing --json. Ignoring that
# surface would mean the observer only ever fires for machine-shaped invocations — which is to
# say, almost never. These patterns were read off real `sf` output against a Developer Edition
# org, not inferred; the two marked `live` were reproduced while writing this.
_MESSAGE_CODES = (
    (r"No such column '[^']*' on entity", "INVALID_FIELD"),                    # live
    (r"Required fields are missing", "REQUIRED_FIELD_MISSING"),                # live
    (r"[Nn]o such column|[Ii]nvalid field", "INVALID_FIELD"),
    (r"insufficient access rights on", "INSUFFICIENT_ACCESS_OR_READONLY"),
    (r"duplicate value found", "DUPLICATE_VALUE"),
    (r"unable to obtain exclusive access to this record", "UNABLE_TO_LOCK_ROW"),
    (r"storage limit exceeded", "STORAGE_LIMIT_EXCEEDED"),
    (r"entity is deleted", "ENTITY_IS_DELETED"),
    (r"[Mm]alformed query|unexpected token", "MALFORMED_QUERY"),
    (r"INVALID_SESSION_ID|Session expired or invalid", "INVALID_SESSION_ID"),
)
_MESSAGE_RES = [(re.compile(p), c) for p, c in _MESSAGE_CODES]
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def platform_error(body):
    """The Salesforce error code this output represents, or None if it is not one.

    Returns (code, offset) so the caller can excerpt around the failure.
    """
    m = _ERR_RE.search(body)
    if m:
        return m.group(1), m.start()
    for rx, code in _MESSAGE_RES:
        m = rx.search(body)
        if m:
            return code, m.start()
    return None, 0


def _shape(cmd):
    """The operation's shape: verb path plus sObject, if either is discernible.

    Two commands share a shape when they are the same operation against the same object — which
    is what makes a later success the answer to an earlier failure rather than a coincidence.
    """
    toks = cmd.split()
    verb = [t for t in toks[1:5] if t and not t.startswith("-")][:3]
    obj = ""
    m = re.search(r"--sobject[= ]+([A-Za-z0-9_]+)|-s([A-Za-z0-9_]+)\b", cmd)
    if m:
        obj = m.group(1) or m.group(2) or ""
    if not obj:
        m = re.search(r"\bFROM\s+([A-Za-z0-9_]+)", cmd, re.I)
        obj = m.group(1) if m else ""
    return ":".join(verb) + "|" + obj


def _append(rec):
    CANDIDATES.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(CANDIDATES.parent, 0o700)
    with open(CANDIDATES, "a") as fh:
        fh.write(json.dumps(rec) + "\n")
    os.chmod(CANDIDATES, 0o600)


def _pending():
    if not CANDIDATES.exists():
        return []
    out = []
    for line in CANDIDATES.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def main():
    ev = lib.read_event()
    if ev.get("tool_name") != "Bash":
        lib.allow()
    cmd = (ev.get("tool_input") or {}).get("command", "")
    if not re.search(r"(^|[|;&(\s])sf\s", cmd):
        lib.allow()

    resp = ev.get("tool_response") or {}
    if isinstance(resp, str):
        body, failed = resp, None
    else:
        body = " ".join(str(resp.get(k, "")) for k in ("stdout", "stderr", "output", "error"))
        failed = resp.get("exit_code") or resp.get("exitCode")
    # Terminal colouring would otherwise travel all the way into a catalogue entry.
    body = _ANSI.sub("", lib.redact(body))[:4000]

    code, at = platform_error(body)
    shape = _shape(cmd)

    if code:
        # The platform refused, and named why. Record the half we have.
        _append({"kind": "failure", "at": int(time.time()), "shape": shape,
                 "code": code, "command": lib.redact(cmd)[:400],
                 "excerpt": body[max(0, at - 120):at + 240].strip()})
    elif failed in (0, None, "0"):
        # A success. If this shape failed earlier and was never resolved, the pair is the lesson.
        for rec in reversed(_pending()):
            if rec.get("kind") == "failure" and rec.get("shape") == shape and not rec.get("paired"):
                _append({"kind": "resolution", "at": int(time.time()), "shape": shape,
                         "code": rec["code"], "failed_command": rec["command"],
                         "working_command": lib.redact(cmd)[:400], "excerpt": rec["excerpt"]})
                # mark the failure paired, so one fix does not claim every earlier failure
                lines = CANDIDATES.read_text().splitlines()
                for i, ln in enumerate(lines):
                    try:
                        d = json.loads(ln)
                    except Exception:
                        continue
                    if d.get("at") == rec["at"] and d.get("kind") == "failure":
                        d["paired"] = True
                        lines[i] = json.dumps(d)
                CANDIDATES.write_text("\n".join(lines) + "\n")
                os.chmod(CANDIDATES, 0o600)
                break
    lib.allow()


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        import tempfile
        ok = True
        with tempfile.TemporaryDirectory() as td:
            CANDIDATES = Path(td) / "c.jsonl"
            globals()["CANDIDATES"] = CANDIDATES
            # a platform error is captured
            _append({"kind": "failure", "at": 1, "shape": "data:create|Account",
                     "code": "INVALID_FIELD", "command": "sf ...", "excerpt": "x"})
            ok &= len(_pending()) == 1
            # shape pairs verb+object, and distinguishes objects
            ok &= _shape("sf data create record --sobject Account -x") == \
                _shape("sf data create record --sobject Account -y")
            ok &= _shape("sf data create record --sobject Account") != \
                _shape("sf data create record --sobject Contact")
            # a shell failure is NOT a platform signal
            # a shell failure is NOT a platform signal; a platform message IS, on both surfaces
            ok &= _ANSI.sub("", "\x1b[31mError\x1b[0m (1)") == "Error (1)"
            ok &= platform_error("bash: sf: command not found")[0] is None
            ok &= platform_error('{"name":"INVALID_FIELD"}')[0] == "INVALID_FIELD"
            ok &= platform_error("No such column 'X__c' on entity 'Account'")[0] == "INVALID_FIELD"
            ok &= platform_error("Error (1): Required fields are missing: [LastName]")[0] \
                == "REQUIRED_FIELD_MISSING"
        print("lesson_observer self-test:", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
    lib.run_gate(main, "lesson_observer")
