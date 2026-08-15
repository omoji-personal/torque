// Render probe: does a NAMED NON-ADMIN USER actually see a field on a record page?
//
// This is the production form of harness/experiments/login-as-render.mjs, which spent
// 2026-08-04 discovering — and recording — every way this measurement goes wrong. The
// archaeology lives there; the conclusions live here, each as one working line:
//
//   · Impersonation goes through the su servlet directly, never the Setup "Login" link
//     (Classic iframe inside Lightning shadow DOM; locators cannot reach it). The user id
//     must be truncated to FIFTEEN characters, and the servlet lands in Classic, so the
//     session is then navigated to Lightning or what gets measured is a Classic page.
//   · Field labels live inside ~564 open shadow roots. page.content(), textContent and
//     allTextContents() all return nothing there; only locators pierce. Four detector
//     designs failed before anyone dumped what was actually rendered.
//   · Record pages want lightning.force.com; the frontdoor lands on my.salesforce.com.
//     "Sorry to interrupt / CSS Error" is a persistent global overlay on this org and is
//     NOT evidence of failure — the record renders underneath it.
//   · `headless: true` alone selects the stripped headless SHELL; channel 'chromium' gets
//     the full browser.
//   · The "Logged in as" banner renders later than the shell and misses ~1 run in 3 even
//     polled — so it is ONE of the impersonation signals here, never the only one, and the
//     caller decides what an unproven impersonation means (nothing green, ever).
//   · Settled-and-absent needs its own evidence: a rendered record header proves the page
//     arrived, and only then does the field's absence mean "not permitted" rather than
//     "not loaded yet". null means never settled and must not be rounded to either.
//
//   node render_probe.mjs <url-file> <instance-url> <org-id-18> <target-user-id> \
//                         <record-id> <object> <field-label>
//
// The url-file holds a LIVE session token: it is read and unlinked immediately, so its
// lifetime does not depend on which branch returns (the browser_probe.mjs rule). Emits
// TORQUE~key=value marks on stdout and NOTHING else; the caller (bin/torque-done) owns the
// mapping from marks to a ledger outcome, so there is exactly one place that decides what
// green means. No screenshots: capture.py is the only image writer in this repository.
import { chromium } from 'playwright';
import { readFileSync, unlinkSync } from 'fs';

const [, , urlFile, instanceUrl, orgId18, targetUserId, recordId, objectName, fieldLabel]
  = process.argv;
if (!urlFile || !instanceUrl || !orgId18 || !targetUserId || !recordId || !objectName
    || !fieldLabel) {
  console.log('TORQUE~error=usage: url-file instance-url org-id-18 target-user-id '
              + 'record-id object field-label');
  process.exit(2);
}
const url = readFileSync(urlFile, 'utf8').trim();
try { unlinkSync(urlFile); } catch {}

const t0 = Date.now();
const mark = (k, v) => console.log(`TORQUE~${k}=${v}`);

// Impersonating your own session is a no-op that renders perfectly and proves nothing —
// done by accident once, and the answer was a confident boolean about a meaningless
// question. The caller passes the session's own 15-char user id so this refuses instead.
const uid15 = targetUserId.slice(0, 15);
if (process.env.TORQUE_SESSION_USER_15 &&
    process.env.TORQUE_SESSION_USER_15.slice(0, 15) === uid15) {
  mark('error', 'target is the session user; impersonating yourself measures nothing');
  mark('done', 1);
  process.exit(0);
}

// ONE matcher for "is this session impersonating", used at every measurement point —
// two inline copies of this drifted by one alternative once and turned a successful hop
// into a reported failure.
const impersonationMarker = async (page, tries = 10) => {
  for (let i = 0; i < tries; i++) {
    try {
      if (await page.locator("a[href*='servlet.sulogout']").count() > 0) return true;
      if (await page.getByText(/logged in as|log ?out as/i).count() > 0) return true;
    } catch (e) { /* keep polling */ }
    await page.waitForTimeout(1500);
  }
  return false;
};

const LIGHTNING_HOST = (process.env.TORQUE_LIGHTNING_HOST || '').replace(/\/$/, '');
const uiHost = () =>
  LIGHTNING_HOST || instanceUrl.replace('.my.salesforce.com', '.lightning.force.com');

// THE WALK, not getByText. The experiment's solved section is explicit: the field labels
// are unreachable by every Playwright text API on this page — page.content() serialises
// the light DOM, textContent and getByText return nothing across ~564 open shadow roots,
// and the elements MATCH while reading as empty string. What works is an explicit
// recursive shadowRoot walk inside page.evaluate, where native DOM textContent sees them.
// The first draft of this file ported the text-API detector anyway, and the live ADMIN
// control caught it on its first run: adminFieldVisible=false for a session that held the
// grant, which is the detector failing its control, not the org hiding a field.
const fieldLabels = async (page) => page.evaluate(() => {
  const out = [];
  const walk = (r) => {
    r.querySelectorAll('*').forEach((e) => { if (e.shadowRoot) walk(e.shadowRoot); });
    r.querySelectorAll('.test-id__field-label').forEach((e) => {
      const t = (e.textContent || '').trim();
      if (t) out.push(t);
    });
  };
  walk(document);
  return out;
});

// true = visible to this session, false = rendered labels without it, null = never settled.
const fieldVisible = async (page) => {
  try {
    await page.goto(`${uiHost()}/lightning/r/${objectName}/${recordId}/view`,
                    { waitUntil: 'domcontentloaded', timeout: 30000 });
  } catch (e) { /* a redirect mid-navigation is normal; the poll below settles it */ }
  // The CSS Error dialog offers "Refresh"; doing what the page asks is the remedy
  // Salesforce itself prints, and treating the dialog as fatal cost five detector designs.
  try {
    for (let attempt = 0; attempt < 2; attempt++) {
      const errored = await page.getByText(/CSS Error/i).count() > 0;
      if (!errored) break;
      await page.reload({ waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForTimeout(4000);
    }
  } catch (e) { /* a reload racing a redirect is not a failure */ }
  let clickedDetails = false;
  for (let i = 0; i < 12; i++) {
    try {
      const labels = await fieldLabels(page);
      if (labels.some((l) => l.includes(fieldLabel))) return true;
      if (labels.length > 0) {
        // Labels rendered and ours is not among them — one more pass for a late field,
        // then the absence is an observation rather than a page that has not arrived.
        await page.waitForTimeout(2500);
        const again = await fieldLabels(page);
        if (again.some((l) => l.includes(fieldLabel))) return true;
        if (again.length > 0) return false;
      } else if (!clickedDetails) {
        // Zero field labels can mean the record page opened on a tab that has none —
        // the ledger's own procedure note says "open the record, click Details, then
        // walk". One click, once, and only when nothing has rendered yet.
        clickedDetails = true;
        try {
          await page.getByText('Details', { exact: true }).first()
                    .click({ timeout: 3000 });
          await page.waitForTimeout(2500);
        } catch (e) { /* no Details tab is not a failure; keep polling */ }
      }
    } catch (e) { /* a read racing a redirect is a retry, not a failure */ }
    await page.waitForTimeout(1500);
  }
  return null;                                    // never settled — say so, do not guess
};

const shell = async (page) => {
  // Never networkidle on Lightning — it polls forever. Poll a concrete marker instead.
  for (let i = 0; i < 30; i++) {
    if (await page.locator('one-app-nav-bar').count() > 0) return true;
    await page.waitForTimeout(2000);
  }
  return false;
};

const CHANNEL = process.env.TORQUE_BROWSER_CHANNEL || 'chromium';
const browser = await chromium.launch(
  CHANNEL === 'default' ? { headless: true } : { headless: true, channel: CHANNEL });
mark('browserChannel', CHANNEL);
try {
  const page = await browser.newPage({
    viewport: { width: 1680, height: 1050 },      // record pages fail at the headless default
    deviceScaleFactor: 1,
  });

  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 90000 });
  const adminOk = await shell(page);
  mark('adminShell', adminOk);
  mark('adminMs', Date.now() - t0);
  if (!adminOk) { mark('done', 1); process.exit(0); }

  // The control: the admin must SEE the field, or the detector cannot be trusted to say
  // anything about the impersonated session — "the user was denied" and "the selector is
  // broken" would be the same boolean. The vacuous-mutator rule, applied to a browser.
  const adminSees = await fieldVisible(page);
  mark('adminFieldVisible', adminSees);

  // ── the hop ──
  const t1 = Date.now();
  const su = `${instanceUrl}/servlet/servlet.su?oid=${orgId18}` +
             `&suorgadminid=${uid15}&retURL=%2F&targetURL=%2F`;
  await page.goto(su, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(2500);                // impersonation handshake
  // Measured HERE, in Classic, before the Lightning hop: a false result downstream would
  // have two indistinguishable causes. Only the path is recorded — never the query string,
  // which carries session material.
  const suPath = new URL(page.url()).pathname;
  const classicMarker = await impersonationMarker(page, 4);
  mark('suLandedPath', suPath);
  mark('classicImpersonationMarker', classicMarker);

  await page.goto(`${instanceUrl}/lightning/page/home`,
                  { waitUntil: 'domcontentloaded', timeout: 30000 });
  const asUserOk = await shell(page);
  mark('asUserShell', asUserOk);
  mark('hopMs', Date.now() - t1);

  const lightningMarker = await impersonationMarker(page);
  mark('impersonating', lightningMarker);

  const userSees = await fieldVisible(page);
  mark('asUserFieldVisible', userSees);

  mark('totalMs', Date.now() - t0);
  mark('done', 1);
} catch (e) {
  mark('error', String(e).slice(0, 160).replace(/\s+/g, ' '));
  mark('done', 1);
} finally {
  await browser.close();
}
