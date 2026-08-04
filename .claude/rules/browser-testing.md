# Browser testing

Auth is a **frontdoor handoff from the CLI session** — never a UI login. Salesforce enforces
phishing-resistant MFA (enforcement began 2026-07-20, staggered over roughly 15 days, for
admin-class users; the earlier 1 July date was PAUSED over a security-key re-registration bug and
then restarted, not withdrawn), so UI login in automation hits a WebAuthn wall;
`sf org open --url-only` mints a session URL from the already-authenticated CLI. That URL
**contains a live session token — it must never be echoed** to stdout, logs, or a
screenshot. Use `bin/torque-frontdoor` (writes it to a mode-0600 file, prints nothing).

- Never `wait_for("networkidle")` on Lightning — it polls forever. Wait for a concrete
  marker (`one-app-nav-bar`, the record header).
- Never XPath against Lightning — it doesn't pierce shadow DOM. Use role/text/data-* .
- Never click anything that fires a native `alert/confirm/prompt` — it freezes the driver.
- UAT only against TEST-prefixed records, never real client data.
- **Screenshots:** no full-window / browser-chrome captures (the URL bar carries the
  session token). `capture.py` is the only writer — it crops to the component, strips EXIF,
  and binds each image to a manifest (SHA-256 + crop region + disposable-org Id).
- No browser / device activation ⇒ the check is **BLOCKED with a dated reason** (non-green,
  DEGRADED) — never faked green.

ENFORCEMENT: harness-enforced (frontdoor_noecho)
