# The dispatch that decides scope, exercised — because twice now it was patched and shipped
# with no check, and the second incident arrived through the hole beside the first fix.
#
# 2026-08-05: `install-gates --project` did not exist, fell through to the user-level default,
# and registered hooks machine-wide. The fix refused unknown FLAGS and shipped uncheckd.
# 2026-08-10: `install-gates --shim <stray>` — a trailing shell comment interactive zsh does not
# strip — made the --shim branch's argv-length guard false and fell through to the same default,
# which this time also installed the shim, machine-wide, with nobody at the keyboard. Same
# shape, one spelling over, five days later. A guard on this dispatch that no check exercises
# is a guard whose next hole is already scheduled.
#
# Everything here runs the real installer as a subprocess with HOME and TORQUE_ANCHOR pointed
# at throwaway directories, stdin closed and a new session — the same isolation
# check_bypass.py uses — so presence is deterministically false whoever launched the harness,
# and nothing can touch the operator's real settings.json or anchor.
import subprocess as _id_sp
import sys as _id_sys
import tempfile as _id_tmp
import os as _id_os
from pathlib import Path as _id_Path

_ID_TOOL = ROOT / "bin" / "torque-install-gates"


@check("install_dispatch_is_guarded", "static", catastrophe=True)
def _install_dispatch_is_guarded():
    """No argv the operator did not name may reach the machine-wide default.

    Four directions, refusals and an acceptance, because "refuse everything" passes any
    refusal-only test:
      1. `--shim <stray>` refuses, names the stray token, installs nothing — the exact argv of
         the 2026-08-10 incident, which on the unguarded tree performed a user-level install.
      2. An unknown flag still refuses (the 2026-08-05 fix must survive the 2026-08-13 one).
      3. The bare user-level install demands an operator: without presence it exits 2, writes no
         settings.json, and says so. (The accept direction of presence itself is
         `operator_presence_can_succeed`'s subject, not this check's — a login session must not
         become forgeable to satisfy a test.)
      4. `--shim` alone still installs the shim into the anchor and touches no settings.json —
         the legitimate form of the refused command goes through, and looks like real use.
    """
    name = "install_dispatch_is_guarded"
    bad = []
    with _id_tmp.TemporaryDirectory() as td:
        home = _id_Path(td) / "home"
        anchor = _id_Path(td) / "anchor"
        home.mkdir()
        settings = home / ".claude" / "settings.json"
        env = {**_id_os.environ, "HOME": str(home), "TORQUE_ANCHOR": str(anchor)}

        def run(*argv):
            return _id_sp.run([_id_sys.executable, str(_ID_TOOL), *argv],
                              capture_output=True, text=True, cwd=str(ROOT), env=env,
                              stdin=_id_sp.DEVNULL, start_new_session=True)

        # 1 — the incident argv. Refusal must come BEFORE any install on either surface.
        r = run("--shim", "x")
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 2:
            bad.append(f"--shim x: exit {r.returncode}, wanted 2 — the 2026-08-10 argv still "
                       f"reaches an install")
        elif "'x'" not in out and '"x"' not in out and " x " not in out:
            bad.append(f"--shim x: refused without naming the stray token, said "
                       f"{out.strip()[:60]!r}")
        if (anchor / "shim" / "sf").exists():
            bad.append("--shim x: the shim was INSTALLED despite the refusal")
        if settings.exists():
            bad.append("--shim x: settings.json was WRITTEN — the fall-through to the "
                       "user-level default is live")

        # 2 — the 2026-08-05 guard must survive.
        r = run("--bogus")
        if r.returncode != 2 or "--bogus" not in ((r.stdout or "") + (r.stderr or "")):
            bad.append(f"--bogus: exit {r.returncode}, unknown flags no longer refuse by name")

        # 3 — the machine-wide default is operator-only.
        r = run()
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 2 or "operator" not in out.lower():
            bad.append(f"bare install: exit {r.returncode}, said {out.strip()[:60]!r} — the "
                       f"user-level install runs unattended")
        if settings.exists():
            bad.append("bare install: settings.json was written without an operator")

        # 4 — the allow direction, shaped like real use.
        r = run("--shim")
        missing = [n for n in ("sf", "sfdx", "home") if not (anchor / "shim" / n).exists()]
        if r.returncode != 0 or missing:
            bad.append(f"--shim alone: exit {r.returncode}, missing {missing or 'nothing'} — "
                       f"the legitimate spelling no longer installs")
        if settings.exists():
            bad.append("--shim alone: settings.json was written — shim-only is not shim-only")

    if bad:
        return Result(name, FAIL, "; ".join(bad))
    return Result(name, PASS,
                  "the 2026-08-10 argv (--shim <stray>) refuses naming the token and installs "
                  "nothing on either surface, unknown flags still refuse, the bare user-level "
                  "install demands an operator, and --shim alone installs the shim into the "
                  "anchor without touching settings.json")
