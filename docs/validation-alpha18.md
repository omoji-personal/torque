# Validation for alpha 18

Alpha 18 returns to Lightning after Logout As before checking the original browser
user, and includes redacted preflight failures in JSON cell results. The actual user
ID must still match. This does not relax connected-mode approval or identity checks.

## Review scope

macOS with Python 3.14.7. Regression tests model a Classic landing page after either
logout selector and reject a session that still belongs to another user. The tests
fail against the prior implementation. All 186 browser pytest tests pass.

The installed-source offline run passed 3,467 tests and 154 subtests with 7 skips;
four release-document checks failed because the README still named the previous
version. Those four checks passed after updating the README. All twelve standalone
fixture suites passed. Offline sentinels handled expected unavailable-tool cases;
no live org command ran in these checks.

The wheel and source distribution pass the distribution checker. A clean wheel
installation passes dependency checks and the installed CLI smoke suite. Original
package source hashes are retained; changed destination hashes are updated.

## Remaining acceptance

The CI matrix and a live Login As, journey and Logout As run remain unverified until
their results are recorded. Simulated browser pages do not establish live Salesforce
behavior, rendered staff access or org-specific reliability. No package-index release
or demo readiness is claimed.
