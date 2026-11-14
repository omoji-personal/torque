"""Read a saved result of `sf project deploy start|validate|report --json`.

Tolerates what saved results look like in practice: a UTF-8 byte-order mark, UTF-16 written by
Windows PowerShell redirection, CLI warning lines before the JSON object (stderr merged into the
file), trailing text after it, and the result with or without the CLI's
`{"status": ..., "result": ...}` envelope. Nothing here calls an org.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os

STATES = ("Created", "Changed", "Unchanged", "Deleted", "Failed")
DEPLOY_KEYS = ("checkOnly", "numberComponentsTotal", "numberComponentsDeployed", "numberComponentErrors")
DETAIL_KEYS = ("componentSuccesses", "componentFailures", "runTestResult")


class ResultError(ValueError):
    """The file could not be read as a deploy result; the message names the likely cause."""


@dataclass
class Entry:
    type: str
    name: str
    state: str                 # one of STATES
    file_path: str = ""        # as the CLI wrote it (local path for `files`, package path for details)
    problem: str = ""
    problem_type: str = ""
    line: int | None = None
    column: int | None = None


@dataclass
class DeployResult:
    raw: dict
    path: str
    id: str = ""
    status: str = ""
    success: bool | None = None
    done: bool | None = None
    check_only: bool | None = None
    target_org: str = ""
    components: list[Entry] = field(default_factory=list)
    failures: list[Entry] = field(default_factory=list)
    test_failures: list[dict] = field(default_factory=list)
    coverage_warnings: list[dict] = field(default_factory=list)
    tests_run: int | None = None
    cli_error: str = ""        # the envelope's error name and message, when it carries one
    read_notes: list[str] = field(default_factory=list)
    declared: dict = field(default_factory=dict)   # total, deployed, failed as the result states them (None: not)
    detail: bool = False       # the file has a per-component list (files or component details)

    def counts(self) -> dict:
        """Components by state. Without a per-component list the states are unknown (None), never zero; failed
        is then the count the result states, when it states one."""
        if not self.detail and self.declared.get("total") != 0:
            return {**{s.lower(): None for s in STATES}, "failed": self.declared.get("failed")}
        out = {s.lower(): 0 for s in STATES}
        for e in self.components:
            out[e.state.lower()] = out.get(e.state.lower(), 0) + 1
        return out


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _flag(value) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def decode(data: bytes) -> tuple[str, list[str]]:
    """Text of the file and notes on what had to be tolerated."""
    notes = []
    if data.startswith(b"\xef\xbb\xbf"):
        notes.append("byte-order mark (UTF-8)")
        return data[3:].decode("utf-8", errors="replace"), notes
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        notes.append("UTF-16 (as Windows PowerShell writes a redirect)")
        return data.decode("utf-16", errors="replace"), notes
    if len(data) >= 4 and data[1:2] == b"\x00" and data[3:4] == b"\x00":
        notes.append("UTF-16 without a byte-order mark")
        return data.decode("utf-16-le", errors="replace"), notes
    return data.decode("utf-8", errors="replace"), notes


def _first_object(text: str) -> tuple[dict | None, bool]:
    """The first JSON object in the text; True when non-JSON text came before it."""
    decoder = json.JSONDecoder()
    start = 0
    while True:
        i = text.find("{", start)
        if i < 0:
            return None, False
        try:
            value, _end = decoder.raw_decode(text, i)
        except ValueError:
            start = i + 1
            continue
        if isinstance(value, dict):
            return value, bool(text[:i].strip())
        start = i + 1


def _is_deploy(obj: dict) -> bool:
    if any(k in obj for k in DEPLOY_KEYS):
        return True
    details = obj.get("details")
    if isinstance(details, dict) and any(k in details for k in DETAIL_KEYS):
        return True
    return str(obj.get("id") or "").startswith("0Af") and "status" in obj


def read(path: str) -> DeployResult:
    """Read and normalise a saved deploy result. Raises ResultError naming the likely cause."""
    if not os.path.isfile(path):
        raise ResultError(f"no such file: {path}. If another shell wrote it, that shell's working folder may "
                          "differ; give the full path.")
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise ResultError(f"could not read {path}: {exc.strerror or exc}") from None
    if not data.strip(b"\x00\r\n\t \xef\xbb\xbf\xff\xfe"):
        raise ResultError(f"{path} is empty: the command may have failed before it wrote anything, or its "
                          "output went through a pipe. Run it again with --json and redirect it to the file.")
    text, notes = decode(data)
    obj, leading = _first_object(text)
    if obj is None:
        raise ResultError(f"{path} holds no JSON object: it looks like the CLI's human-readable output. Run the "
                          "command again with --json and write its output to the file.")
    if leading:
        notes.append("text before the JSON (a CLI warning merged into the file)")
    result = obj
    cli_error = ""
    if isinstance(obj.get("result"), dict):
        result = obj["result"]
    elif isinstance(obj.get("data"), dict) and _is_deploy(obj["data"]):
        result = obj["data"]
    if not _is_deploy(result):
        if "status" in obj and ("message" in obj or "name" in obj) and not isinstance(obj.get("result"), dict):
            raise ResultError(f"{path} holds a CLI error, not a deploy result: "
                              f"{obj.get('name', 'Error')}: {' '.join(str(obj.get('message', '')).split())[:240]}")
        raise ResultError(f"{path} is JSON but not a deploy result (no deploy id, component details or "
                          "counts). It may be a retrieve or query result; give the file written by "
                          "`sf project deploy start|validate|report --json`.")
    if obj is not result and obj.get("status") not in (0, None) and obj.get("message"):
        cli_error = f"{obj.get('name', 'Error')}: {' '.join(str(obj['message']).split())[:240]}"
    return _normalise(result, path, notes, cli_error)


def _normalise(r: dict, path: str, notes: list[str], cli_error: str) -> DeployResult:
    out = DeployResult(raw=r, path=path, id=str(r.get("id") or ""), status=str(r.get("status") or ""),
                       success=(_flag(r["success"]) if "success" in r else None),
                       done=(_flag(r["done"]) if "done" in r else None),
                       check_only=(_flag(r["checkOnly"]) if "checkOnly" in r else None),
                       cli_error=cli_error, read_notes=notes)
    for key in ("targetOrg", "username", "targetUsername"):
        if isinstance(r.get(key), str) and r[key]:
            out.target_org = r[key]
            break
    details = r.get("details") if isinstance(r.get("details"), dict) else {}
    out.declared = {"total": _int(r.get("numberComponentsTotal")), "deployed": _int(r.get("numberComponentsDeployed")),
                    "failed": _int(r.get("numberComponentErrors"))}
    out.detail = r.get("files") is not None or any(details.get(k) is not None
                                                    for k in ("componentSuccesses", "componentFailures"))
    seen: dict[tuple[str, str], Entry] = {}

    def add(entry: Entry):
        if not entry.type or not entry.name or entry.name == "package.xml":
            return
        key = (entry.type, entry.name.lower())
        old = seen.get(key)
        if old is None:
            seen[key] = entry
            out.components.append(entry)
        elif entry.state == "Failed" and old.state != "Failed":
            old.state = "Failed"

    for f in _as_list(r.get("files")):
        if isinstance(f, dict) and f.get("state") in STATES:
            add(Entry(str(f.get("type") or ""), str(f.get("fullName") or ""), f["state"],
                      file_path=str(f.get("filePath") or ""), problem=str(f.get("error") or ""),
                      problem_type=str(f.get("problemType") or ""), line=_int(f.get("lineNumber")),
                      column=_int(f.get("columnNumber"))))
    for s in _as_list(details.get("componentSuccesses")):
        if not isinstance(s, dict):
            continue
        state = ("Deleted" if _flag(s.get("deleted")) else "Created" if _flag(s.get("created"))
                 else "Changed" if _flag(s.get("changed")) else "Unchanged")
        add(Entry(str(s.get("componentType") or ""), str(s.get("fullName") or ""), state,
                  file_path=str(s.get("fileName") or "")))
    for s in _as_list(details.get("componentFailures")):
        if not isinstance(s, dict):
            continue
        entry = Entry(str(s.get("componentType") or ""), str(s.get("fullName") or ""), "Failed",
                      file_path=str(s.get("fileName") or ""), problem=" ".join(str(s.get("problem") or "").split()),
                      problem_type=str(s.get("problemType") or ""), line=_int(s.get("lineNumber")),
                      column=_int(s.get("columnNumber")))
        out.failures.append(entry)
        add(Entry(entry.type, entry.name, "Failed", file_path=entry.file_path))
    if not out.failures:
        for e in _as_list(r.get("files")):
            if isinstance(e, dict) and e.get("state") == "Failed":
                out.failures.append(Entry(str(e.get("type") or ""), str(e.get("fullName") or ""), "Failed",
                                          file_path=str(e.get("filePath") or ""),
                                          problem=" ".join(str(e.get("error") or "").split()),
                                          problem_type=str(e.get("problemType") or ""),
                                          line=_int(e.get("lineNumber")), column=_int(e.get("columnNumber"))))
    tests = details.get("runTestResult") if isinstance(details.get("runTestResult"), dict) else {}
    out.tests_run = _int(tests.get("numTestsRun")) if tests else _int(r.get("numberTestsCompleted"))
    for t in _as_list(tests.get("failures")):
        if isinstance(t, dict):
            out.test_failures.append({
                "class": str(t.get("name") or ""), "method": str(t.get("methodName") or ""),
                "message": " ".join(str(t.get("message") or "").split()),
                "stack": " ".join(str(t.get("stackTrace") or "").split())})
    for w in _as_list(tests.get("codeCoverageWarnings")):
        if isinstance(w, dict):
            out.coverage_warnings.append({"name": str(w.get("name") or "") if not isinstance(w.get("name"), dict)
                                          else "", "message": " ".join(str(w.get("message") or "").split())})
    return out
