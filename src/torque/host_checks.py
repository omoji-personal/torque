"""Read-only host checks for `torque doctor`: facts about this machine that make
agent work fail in ways that look like Torque or Salesforce errors. Nothing here
changes a setting, updates a tool or calls an org: the Salesforce CLI is asked only
for its version, with a short timeout."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

SF_TIMEOUT = 20


def _long_paths() -> bool | None:
    """Windows long paths (LongPathsEnabled); None where it does not apply or cannot be read."""
    if os.name != "nt":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            return bool(winreg.QueryValueEx(key, "LongPathsEnabled")[0])
    except OSError:
        return None


def _powershell() -> str | None:
    """Which PowerShell a host shell would start: Windows PowerShell 5.1 (powershell) or
    PowerShell 7 (pwsh). Found on PATH only; nothing is started."""
    if os.name != "nt":
        return None
    if shutil.which("pwsh"):
        return "PowerShell 7 (pwsh) on PATH" + (", Windows PowerShell 5.1 too" if shutil.which("powershell") else "")
    return "Windows PowerShell 5.1 only (pwsh not on PATH)" if shutil.which("powershell") else "none found"


def _sf(run=subprocess.run) -> dict:
    """Two facts about the Salesforce CLI: whether `sf` resolves and answers
    `sf --version --json`, and whether that JSON output is clean (one JSON value, no
    update warning or other text mixed in, which breaks every `--json` read)."""
    path = shutil.which("sf")
    if not path:
        return {"resolves": False, "path": None, "json_clean": None, "detail": "sf is not on PATH"}
    try:
        done = run([path, "--version", "--json"], capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=SF_TIMEOUT, env={**os.environ, "SF_AUTOUPDATE_DISABLE": "true"})
    except (OSError, subprocess.SubprocessError) as exc:
        return {"resolves": False, "path": path, "json_clean": None,
                "detail": f"sf did not answer ({type(exc).__name__})"}
    out = (done.stdout or "").strip()
    try:
        json.loads(out)
        clean = True
    except ValueError:
        clean = False
    warning = "update" in ((done.stderr or "") + out).lower() and "available" in ((done.stderr or "") + out).lower()
    detail = ("its --json output holds other text than one JSON value" if not clean else
              "an update notice is printed on stderr (JSON on stdout is clean)" if warning else "")
    return {"resolves": done.returncode == 0, "path": path, "json_clean": clean, "update_notice": warning,
            "detail": detail}


def report(run=subprocess.run) -> dict:
    return {"utf8_mode": bool(sys.flags.utf8_mode), "stdout_encoding": getattr(sys.stdout, "encoding", None),
            "salesforce_cli": _sf(run), "long_paths": _long_paths(), "powershell": _powershell()}


def lines(host: dict) -> list[str]:
    """The host checks as doctor text, one fact per line (report only)."""
    sf = host["salesforce_cli"]
    out = [f"Python UTF-8 mode: {'on' if host['utf8_mode'] else 'off'} (output encoding {host['stdout_encoding']})"]
    out.append("Salesforce CLI: " + ("resolves and answers --version" if sf["resolves"] else
                                     f"does not resolve ({sf['detail']})"))
    if sf["json_clean"] is not None:
        out.append("Salesforce CLI JSON output: " + ("clean" if sf["json_clean"] else "NOT clean")
                   + (f" ({sf['detail']})" if sf["detail"] else ""))
    if host["long_paths"] is not None:
        out.append("Windows long paths: " + ("enabled" if host["long_paths"] else
                                             "not enabled (paths past 260 characters fail; keep workspaces short)"))
    if host["powershell"]:
        out.append(f"PowerShell: {host['powershell']}")
    return out
