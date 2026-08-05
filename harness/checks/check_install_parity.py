# The user-level install must be the project install, not a weaker cousin.
#
# The installer's docstring says it mirrors the project registration exactly. It did not: the
# project settings.json declares timeout 55 on every gate hook and the installer emitted none on
# the gates, so a user-level install — the one that binds EVERY workspace — registered gates the
# host could kill mid-decision. The enforcement contract is "exit 2 blocks, any other exit
# allows", so a killed gate is an allowed operation. The broader install was the weaker one.
#
# This is the two-lists shape again: two registrations of one contract, and nothing compared them.
import json as _ij
import os as _ios
import shutil as _ish
import subprocess as _isp
import sys as _isys
import tempfile as _itf
from pathlib import Path as _IP


def _gate_hooks(blocks):
    """Every hook entry whose command runs one of the two gates, with its declared timeout."""
    out = []
    for blk in blocks:
        for h in blk.get("hooks", []):
            c = h.get("command", "")
            if "prod_write_gate.py" in c or "destructive_data_gate.py" in c:
                out.append((blk.get("matcher"), h.get("timeout")))
    return out


@check("install_parity_project_and_user", "static", catastrophe=True)
def _install_parity_project_and_user():
    """Run the real installer against a throwaway HOME and compare what it registers.

    Asserts the property that actually matters — every gate hook carries the timeout the project
    declares — rather than asserting the two files are byte-identical, which they never will be
    (the project uses $CLAUDE_PROJECT_DIR, the installer writes absolute paths).
    """
    _isys.path.insert(0, str(ROOT / "hooks"))
    for m in ("lib", "shellparse"):
        _isys.modules.pop(m, None)
    import lib
    want = int(lib.HOOK_TIMEOUT_S)

    proj = _ij.loads((ROOT / ".claude" / "settings.json").read_text())
    proj_gates = _gate_hooks(proj.get("hooks", {}).get("PreToolUse", []))
    bad_proj = [(m, t) for m, t in proj_gates if t != want]
    if bad_proj:
        return Result("install_parity_project_and_user", FAIL,
                      f"the PROJECT registration declares a gate timeout other than "
                      f"{want}: {bad_proj}")

    home = _IP(_itf.mkdtemp(prefix="torque-install-parity-"))
    try:
        env = dict(_ios.environ, HOME=str(home), PYTHONDONTWRITEBYTECODE="1")
        env.pop("TORQUE_SHIM_DEPTH", None)
        r = _isp.run([_isys.executable, str(ROOT / "bin" / "torque-install-gates")],
                     capture_output=True, text=True, env=env, cwd=str(ROOT))
        settings = home / ".claude" / "settings.json"
        if r.returncode != 0 or not settings.exists():
            return Result("install_parity_project_and_user", FAIL,
                          f"the installer did not produce a user-level settings file "
                          f"(exit {r.returncode}): {(r.stderr or r.stdout).strip()[:120]}")
        user = _ij.loads(settings.read_text())
        user_gates = _gate_hooks(user.get("hooks", {}).get("PreToolUse", []))

        if not user_gates:
            return Result("install_parity_project_and_user", FAIL,
                          "the user-level install registered no gate hooks at all")
        missing = [(m, t) for m, t in user_gates if t != want]
        if missing:
            return Result("install_parity_project_and_user", FAIL,
                          f"user-level gate hooks declare a timeout other than {want}: "
                          f"{missing} — a killed gate exits non-2, and non-2 means ALLOW, so the "
                          f"install that binds every workspace is the one that fails open")

        pm = sorted({m for m, _ in proj_gates})
        um = sorted({m for m, _ in user_gates})
        if pm != um:
            return Result("install_parity_project_and_user", FAIL,
                          f"matchers differ — project {pm}, user {um}; silent partial protection "
                          f"is worse than none because it is indistinguishable from the real "
                          f"thing until it matters")
        return Result("install_parity_project_and_user", PASS,
                      f"{len(user_gates)} user-level gate hooks across {len(um)} matcher(s), "
                      f"every one declaring timeout {want}, matching the project registration")
    finally:
        _ish.rmtree(home, ignore_errors=True)
