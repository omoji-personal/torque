// Login-As render probe: can Torque render Lightning AS ANOTHER USER, and what does it cost?
//
// The completion gate's last layer asks whether a change is visible to a non-admin. An admin
// sees everything and proves nothing, so the only render that settles it is one under another
// user's profile. `torque done` reports that layer BLOCKED, gated on three questions: can it
// render as another profile at all, what is the wall-clock cost, and how flaky is it.
//
// THE SETUP UI "LOGIN" LINK IS THE WRONG PATH, and finding that out costs a session.
//
// It is gated by the "Administrators Can Log in as Any User" org preference, it renders inside
// a Classic iframe, and it sits in Lightning shadow DOM — so role and text locators miss it
// even when the preference is on and the link is genuinely there. The direct servlet is the
// reliable path:
//
//     /servlet/servlet.su?oid=<org_id_18>&suorgadminid=<user_id_15>&retURL=%2F&targetURL=%2F
//
// Two details that are not optional: the user Id must be truncated to FIFTEEN characters, and
// the servlet lands in Classic — so an impersonated session must then be navigated to a
// Lightning URL, or what gets measured is a Classic page pretending to be the thing users see.
//
// Reads the frontdoor URL from a 0600 file (argv[2]) and unlinks it immediately, like
// browser_probe.mjs: the file holds a live session token and its lifetime must not depend on
// which branch returns.
//
//   node login-as-render.mjs <url-file> <instance-url> <org-id-18> <target-user-id>
//
// Emits TORQUE~ markers on stdout. No screenshot: capture.py is the only writer in this
// repository, and a probe that quietly grew its own image path would be the second writer.
//
// ── STATE, 2026-08-04. Read this before extending it. ─────────────────────────────────────
//
// ESTABLISHED. Impersonation works via the servlet once `enableAdminLoginAsAnyUser` is on, and
// two independent signals confirm it: the servlet redirects to the retURL target instead of
// sitting on itself, and the "Logged in as" banner appears. Cost is 10–19 seconds end to end.
//
// NOT ESTABLISHED, and the reason the layer this feeds is still BLOCKED: no assertion here is
// reliable enough to put behind a green tick.
//
//   · The banner is the only usable impersonation signal — `servlet.sulogout` is absent from
//     the Lightning DOM entirely — and it renders LATER than the shell. Polled 15s it still
//     returned false on one run in three of a session that was genuinely impersonating.
//   · The privilege differential was the attempt to replace a cosmetic signal with a semantic
//     one, and it has not discriminated. Setup Home failed for a knowable reason: Standard User
//     carries PermissionsViewSetup=true here, so it is not a boundary. Manage Users SHOULD
//     discriminate — PermissionsManageUsers is false for that profile and true for an admin —
//     and still reports ABSENT across four runs with the pattern-overlap bug fixed. Either
//     Lightning renders the refusal in a form none of these patterns match, or the session is
//     not being refused. That is unresolved and is NOT worth more detection-tuning; the next
//     person should capture the actual denial page once and read it, rather than guessing a
//     seventh regex.
//
// Everything here reports ABSENT or UNSETTLED rather than picking a side, which is why this is
// an experiment and not a check.
import { chromium } from 'playwright';
import { readFileSync, unlinkSync } from 'fs';

const [, , urlFile, instanceUrl, orgId18, targetUserId] = process.argv;
if (!urlFile || !instanceUrl || !orgId18 || !targetUserId) {
  console.log('TORQUE~error=usage');
  process.exit(2);
}
const url = readFileSync(urlFile, 'utf8').trim();
try { unlinkSync(urlFile); } catch {}

const t0 = Date.now();
const mark = (k, v) => console.log(`TORQUE~${k}=${v}`);
// ONE matcher for "is this session impersonating", used at both measurement points.
//
// It was written twice, inline, and the two copies drifted by one alternative: the first
// looked for "logged in as" OR "log out as", the second only for "log out as". Salesforce
// renders "Logged in as <name>", so the same live session measured true at the first point and
// false at the second, and the run read as a failed hop that had actually succeeded. Two
// matchers for one question is the defect this repository spent the week removing from
// everything except, it turns out, a probe written the same day.
// POLLED, not sampled once. Measured across six runs: the impersonation banner renders LATER
// than `one-app-nav-bar`, so checking at the moment the shell appears returns false about half
// the time on a session that is genuinely impersonating. That is the entire flake, and it was
// mine — the same mistake as waiting for a marker that has not arrived, made in the opposite
// direction from the shadow experiment's wait, which measured after its evidence expired.
//
// The signal is the "Logged in as" text. `servlet.sulogout` is NOT in the Lightning DOM at all
// (checked: sulogoutInHtml=false while loggedInAsInHtml=true), so the href selector alone would
// never have worked here despite being the obvious one.
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
// The assertion that does not depend on a banner arriving.
//
// The banner approach flaked one run in three even polled for 15s, because "Logged in as"
// renders later than the shell and sometimes not at all within the window. Chasing it further
// would be tuning a signal until it passes rather than picking a better one.
//
// A Standard User cannot reach Setup and an admin can, so ONE navigation separates the two
// sessions on privilege rather than on chrome. It is semantic instead of cosmetic: it proves
// the session both IS somebody else and CAN DO LESS, which is the thing the completion gate
// actually wants to know. It also cannot silently pass while still admin — that is the failure
// mode being designed against, and here it inverts the result rather than hiding in it.
// Manage Users, not Setup Home. The first attempt used Setup Home and the differential came
// back ABSENT — correctly, because Standard User carries PermissionsViewSetup=true in this org
// and Setup Home is therefore not a privilege boundary at all. Asked the org instead of
// guessing a second time: PermissionsManageUsers is false for Standard User and true for an
// admin, so this door discriminates and that one never did.
const SETUP_URL = '/lightning/setup/ManageUsers/home';
const setupDenied = async (page, instanceUrl) => {
  try {
    await page.goto(instanceUrl + SETUP_URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
  } catch (e) { /* a redirect mid-navigation is normal here; the poll below settles it */ }
  for (let i = 0; i < 10; i++) {
    // Setup redirects more than once on the way in, and page.content() throws outright while a
    // navigation is in flight. The first version let that exception escape and killed the whole
    // run on what is the page behaving normally. A read that races a redirect is a retry, not a
    // failure.
    let html = '';
    try { html = await page.content(); } catch (e) { html = ''; }
    if (/insufficient privileges|you do not have permission|not authorized/i.test(html)) {
      return true;
    }
    // The page rendering for real is the other terminal state; stop as soon as either settles.
    //
    // `manage users` was in this list and must not be: it is the breadcrumb, present on the
    // DENIAL page too, so the two patterns overlapped and "rendered" matched a refusal. Only
    // strings that appear when the page genuinely loaded belong here.
    if (/all users|new user|user detail/i.test(html)) return false;
    await page.waitForTimeout(1500);
  }
  return null;                                  // neither state settled — say so, do not guess
};
const shell = async (page) => {
  // Never networkidle on Lightning — it polls forever. Poll for a concrete marker instead.
  for (let i = 0; i < 30; i++) {
    if (await page.locator('one-app-nav-bar').count() > 0) return true;
    await page.waitForTimeout(2000);
  }
  return false;
};

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();

  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 90000 });
  const adminOk = await shell(page);
  mark('adminShell', adminOk);
  mark('adminMs', Date.now() - t0);
  if (!adminOk) { mark('done', 1); process.exit(0); }

  // Baseline: the admin must NOT be denied Setup. Without it, "the impersonated session was
  // denied" could just mean this org denies everyone, and the differential would be measuring
  // nothing — the same vacuous-mutator trap the harness asserts against before every mutation.
  const adminDenied = await setupDenied(page, instanceUrl);
  mark('adminSetupDenied', adminDenied);

  // ── the hop ──
  const t1 = Date.now();
  const uid15 = targetUserId.slice(0, 15);
  // Impersonating your own session is a no-op that renders perfectly and proves nothing. Done
  // by accident on the first real run — the org had two admins and the CLI was authenticated as
  // the one being targeted — and the result was a confident `impersonating=false` answering a
  // question that had no meaning. The caller passes the session's own user so this can refuse
  // rather than measure a tautology.
  if (process.env.TORQUE_SESSION_USER_15 &&
      process.env.TORQUE_SESSION_USER_15.slice(0, 15) === uid15) {
    mark('error', 'target is the session user; impersonating yourself measures nothing');
    mark('done', 1);
    process.exit(0);
  }
  const su = `${instanceUrl}/servlet/servlet.su?oid=${orgId18}` +
             `&suorgadminid=${uid15}&retURL=%2F&targetURL=%2F`;
  await page.goto(su, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(2500);            // impersonation handshake

  // Look for the impersonation marker HERE, in Classic, before the Lightning hop. Without this
  // step a false result downstream has two causes that look identical: the servlet did not
  // impersonate, or it did and the marker is not findable in Lightning. Only the path is
  // recorded, never the query string — the frontdoor URL carries a session token.
  const suPath = new URL(page.url()).pathname;
  const classicMarker = await impersonationMarker(page);
  mark('suLandedPath', suPath);
  mark('classicImpersonationMarker', classicMarker);

  // The servlet lands in Classic. Go to Lightning so what is measured is what a user sees.
  await page.goto(`${instanceUrl}/lightning/page/home`,
                  { waitUntil: 'domcontentloaded', timeout: 30000 });
  const asUserOk = await shell(page);
  mark('asUserShell', asUserOk);
  mark('hopMs', Date.now() - t1);

  // IS THE SESSION ACTUALLY IMPERSONATING? Without this, adminShell=true and asUserShell=true
  // is just "a Lightning page rendered twice" — a hop that silently stayed admin passes every
  // assertion above, which is the whole failure this layer exists to prevent.
  //
  // Salesforce renders a "Log out as" link only while a session is impersonating, so its
  // PRESENCE is the proof. Two spellings because one is the Classic anchor and one is the
  // Lightning global-header item; Playwright's CSS engine pierces open shadow roots, which the
  // href selector needs.
  //
  // Not the REST identity endpoint: that was the first attempt and it returned 156 bytes of
  // non-JSON, because /services/data wants a Bearer token and a browser session carries a
  // cookie. A signal that needs different credentials than the thing being measured is the
  // wrong signal.
  const impersonating = await impersonationMarker(page);
  mark('impersonating', impersonating);
  // The privilege differential, which is the assertion meant to survive.
  const userDenied = await setupDenied(page, instanceUrl);
  mark('asUserSetupDenied', userDenied);
  mark('privilegeDifferential',
       adminDenied === false && userDenied === true ? 'PROVEN'
       : (adminDenied === null || userDenied === null) ? 'UNSETTLED' : 'ABSENT');
  // Decisive diagnostic when the locator and the earlier measurement disagree: is the logout
  // hook in the raw HTML at all? Present-but-not-located is a selector problem; absent is a
  // session that stopped impersonating. Two very different bugs that look identical from a
  // boolean.
  try {
    const html = await page.content();
    mark('sulogoutInHtml', html.includes('sulogout'));
    mark('loggedInAsInHtml', /logged in as/i.test(html));
  } catch (e) { mark('sulogoutInHtml', 'unavailable'); }
  mark('wantIdentity', uid15);
  mark('totalMs', Date.now() - t0);
  mark('done', 1);
} catch (e) {
  mark('error', String(e).slice(0, 160).replace(/\s+/g, ' '));
  mark('done', 1);
} finally {
  await browser.close();
}
