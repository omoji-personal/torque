# P4: frontdoor no-echo handoff (always testable) + browser render E2E (BLOCKED w/ dated
# reason when no browser binary — honest non-green, never faked).
import subprocess as _sp, os as _os, shutil as _sh
from pathlib import Path as _P

@check("frontdoor_noecho", "capability", catastrophe=True)
def _frontdoor_noecho(target):
    if not target: return Result("frontdoor_noecho", SKIP, "no --target-org")
    r = _sp.run(["python3", str(ROOT/"bin"/"torque-frontdoor"), target], capture_output=True, text=True)
    if r.returncode != 0:
        return Result("frontdoor_noecho", FAIL, f"frontdoor failed: {r.stderr[:80]}")
    path = r.stdout.strip()
    # the token must NOT be in stdout; it must be in the 0600 file
    if "frontdoor.jsp" in r.stdout or "sid=" in r.stdout:
        return Result("frontdoor_noecho", FAIL, "session token leaked to stdout")
    try:
        content = _P(path).read_text(); mode = oct(_P(path).stat().st_mode & 0o777)
        _P(path).unlink()
    except Exception as e:
        return Result("frontdoor_noecho", FAIL, f"session file unreadable: {e}")
    if "frontdoor.jsp" not in content:
        return Result("frontdoor_noecho", FAIL, "session file missing the URL")
    if mode != "0o600":
        return Result("frontdoor_noecho", FAIL, f"session file mode {mode} not 0600")
    return Result("frontdoor_noecho", PASS, "session URL in 0600 file; token not echoed")

@check("browser_render", "capability")
def _browser_render(target):
    if not target: return Result("browser_render", SKIP, "no --target-org")
    # need node + a real chromium binary; else HONEST BLOCK (never fake a render)
    cache = _P.home()/"Library"/"Caches"/"ms-playwright"
    have_browser = cache.exists() and any(cache.glob("chromium*"))
    if not (_sh.which("node") and have_browser):
        return Result("browser_render", SKIP,
            "BLOCKED 2026-07-31: no Playwright chromium binary; frontdoor handoff verified "
            "separately. `npx playwright install chromium` to enable the render check.")
    # obtain a no-echo frontdoor URL file, then ACTUALLY launch and assert the Lightning shell
    fr = _sp.run(["python3", str(ROOT/"bin"/"torque-frontdoor"), target], capture_output=True, text=True)
    if fr.returncode != 0:
        return Result("browser_render", FAIL, "frontdoor URL unavailable")
    url_file = fr.stdout.strip()
    probe = ROOT/"harness"/"checks"/"browser_probe.mjs"
    try:
        r = _sp.run(["node", str(probe), url_file], capture_output=True, text=True, timeout=120,
                    cwd=str(ROOT))
    except Exception as e:
        try: _P(url_file).unlink()
        except Exception: pass
        return Result("browser_render", FAIL, f"probe error: {e}")
    if r.returncode == 0 and "RENDER_OK" in r.stdout:
        return Result("browser_render", PASS, f"Lightning shell rendered live ({r.stdout.strip()[:40]})")
    # node present but playwright module or launch failed → honest BLOCK, not FAIL/fake
    if "Cannot find package" in r.stderr or "MODULE_NOT_FOUND" in r.stderr:
        return Result("browser_render", SKIP,
            "BLOCKED 2026-07-31: playwright node module not installed in this workspace; "
            "frontdoor handoff verified separately.")
    return Result("browser_render", FAIL, f"render failed: {(r.stdout + r.stderr).strip()[:90]}")
