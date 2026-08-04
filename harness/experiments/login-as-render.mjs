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
//   · The differential has never discriminated, across FOUR designs, and every failure was in
//     the detector rather than the org. Recorded in order because the pattern is the lesson:
//
//       1. Setup Home — not a boundary. Standard User carries PermissionsViewSetup=true.
//          Found by asking the org. Good failure: it reported ABSENT rather than inventing.
//       2. Manage Users — PermissionsManageUsers is false for that profile, so it should have
//          discriminated. It did not: the page LOADS, title "Users | Salesforce", no refusal.
//          That permission gates actions on users, not access to the page.
//       3. Field visibility via page.content() — content() returns the LIGHT DOM only and
//          Lightning renders labels inside shadow roots, so the search was in a haystack the
//          needle cannot be in. Locators pierce; content() does not. This probably also
//          invalidates 1 and 2, whose refusal patterns were content()-based, so "the door is
//          not locked" may itself have been an artefact.
//       4. Field visibility via locators — still absent for an admin who demonstrably has the
//          field: the granting permission set is assigned to them, the label is exactly
//          "Wholesale Tier" (asked, not derived), and the field was temporarily put on the
//          layout to rule that out. Remaining hypothesis, untested: Lightning record pages put
//          detail fields behind a DETAILS TAB, and the settled-marker matches the highlights
//          panel that renders first — so the check looks for a field on a tab nobody opened.
//
//     Three org states were verified against the org before the detector was suspected, which
//     is the wrong order and cost a layout change to learn. Suspect the measurement first.
//
//   · THEN STOPPED GUESSING AND LOOKED. Dumped every rendered label through locators, which
//     pierce shadow DOM, and the answer was not any of the four hypotheses: the page renders
//     ONLY navigation chrome — Sfdclogo, Search Salesforce, Sales, Home, Opportunities, Leads,
//     Accounts, Contacts, Reports, Chatter, 57 strings, every one of them app furniture — plus
//     "Sorry to interrupt", which is Salesforce's Lightning error dialog.
//
//     There is no record on the record page. There never was a field label to find, in any
//     session, by any selector, and four detectors were refined against a page that had already
//     failed. The next question is why the record does not open — sharing or OWD for the
//     impersonated user is the obvious candidate and is NOT established — and that is a
//     different question from the one this file was written to answer.
//
//     The general lesson, which cost more than the specific one: FOUR selector designs were
//     hypothesised before anything was OBSERVED. One dump would have ended it at the first
//     failure. `TORQUE_DUMP_LABELS=1` runs it.
//
//   · AND THEN READ THE DIALOG BODY. "Sorry to interrupt" is only the header. The body says
//     "CSS Error" — a static-resource failure in the record-page bundle under headless
//     Chromium. Not access: org-wide default is Edit on Account, the profile has Read, the
//     granting permission set is assigned, and the label was confirmed against FieldDefinition.
//     A realistic viewport does not fix it. Lightning HOME and SETUP render fine in the same
//     session, so this is specific to record pages.
//
//     So the field-visibility differential is not achievable headless here, and no selector was
//     ever going to make it work. Four designs were tuned against a page that had already
//     failed for a reason printed on it in two words.
//
//   · RULED OUT, each tested against the same org and record, none of them the cause: a
//     realistic viewport; the full bundled Chromium instead of the headless shell; the system
//     Chrome; navigating on lightning.force.com rather than my.salesforce.com, with the settled
//     URL host and path read back to confirm it landed; and reloading, which is what the error
//     dialog itself offers. It fails identically for the ADMIN, before any impersonation, so it
//     was never about Login As.
//
//     Four configurations returned a byte-identical 57 labels, which is not four confirmations
//     — it is the sign that none of them changed what was being looked at. Checking the settled
//     URL, which took one line and should have been the first thing, was done ninth.
//
// STOP HERE. The bisecting question needs a human: does that same record URL render in the
// operator's ordinary browser? If it fails there too, this is the org or the page layout. If it
// renders, it is the frontdoor-token session or the automation context. Thirty seconds, and no
// amount of selector work substitutes for it.
//
// IMPERSONATION IS CONFIRMED THREE WAYS, which is the one thing that did land: the servlet
// redirects to its retURL target, the "Logged in as" banner appears, and the in-app guidance
// prompt addresses the impersonated user BY NAME. The third is free, appears in the dialog
// dump, and is the least flaky of them.
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
// FIELD VISIBILITY, not a Setup page. Two Setup doors were tried as a proxy for privilege and
// neither is locked: Standard User carries PermissionsViewSetup=true, and ManageUsers gates
// ACTIONS on users rather than access to the page — read directly off the org, the page loads
// with title "Users | Salesforce" and no refusal at all.
//
// The proxy was the mistake. The completion gate's actual question is whether a given profile
// can SEE A FIELD, and field-level security is a real boundary rather than a stand-in for one.
// So the differential is now the measurement: the field label is present on the record page for
// a session that has the permission set and absent for one that does not. Same navigation
// count, and a result that means something on its own rather than by analogy.
const FIELD_LABEL = process.env.TORQUE_FIELD_LABEL || 'Wholesale Tier';
const RECORD_ID = process.env.TORQUE_RECORD_ID || '';
// Lightning serves its assets from lightning.force.com; the frontdoor lands on
// my.salesforce.com. A record page deep-linked on the wrong host renders its chrome and then
// fails to pull the record bundle — which is what "CSS Error" is. Home and Setup survive it,
// record pages do not, and that asymmetry is the tell.
//
// Not a browser problem: the headless shell, the full bundled Chromium and the system Chrome
// all failed identically, and it failed for the ADMIN too, before any impersonation.
const LIGHTNING_HOST = (process.env.TORQUE_LIGHTNING_HOST || '').replace(/\/$/, '');
const uiHost = (instanceUrl) =>
  LIGHTNING_HOST || instanceUrl.replace('.my.salesforce.com', '.lightning.force.com');
// true = the field is visible to this session, false = it is not, null = the page never
// settled, which is neither and must not be rounded to either.
const fieldVisible = async (page, instanceUrl, recordId) => {
  if (!recordId) return null;
  try {
    await page.goto(`${uiHost(instanceUrl)}/lightning/r/Account/${recordId}/view`,
                    { waitUntil: 'domcontentloaded', timeout: 30000 });
  } catch (e) { /* a redirect mid-navigation is normal; the poll below settles it */ }
  // The CSS Error dialog offers "Refresh", and doing what the page asks was never tried across
  // five detector designs. A reload after the first-load asset failure is the remedy Salesforce
  // itself prints; treating it as fatal was an assumption, not an observation.
  try {
    for (let attempt = 0; attempt < 2; attempt++) {
      const errored = await page.getByText(/CSS Error/i).count() > 0;
      if (!errored) break;
      await page.reload({ waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForTimeout(4000);
    }
  } catch (e) { /* a reload racing a redirect is not a failure */ }

  // LOCATORS, not page.content().
  //
  // content() returns the LIGHT DOM only, and Lightning renders field labels inside shadow
  // roots — so `content().includes('Wholesale Tier')` was searching a haystack the needle is
  // never in. It reported the admin as unable to see a field they demonstrably can: the
  // permission set that grants it is assigned to them, and the field is on the layout. Both
  // were checked against the org before suspecting the detector, which is the wrong order and
  // cost an org change to learn.
  //
  // The same mistake almost certainly explains the Setup-denial regexes never matching: those
  // were content()-based too, so a refusal rendered in shadow DOM would have been invisible to
  // them, and "the door is not locked" may itself have been a detection artefact.
  //
  // Playwright's locator engine pierces open shadow roots. getByText does; content() cannot.
  for (let i = 0; i < 12; i++) {
    try {
      if (await page.getByText(FIELD_LABEL, { exact: false }).count() > 0) return true;
      // Settled-and-absent needs its own evidence, or "not found yet" and "not permitted" are
      // the same observation. A rendered record header proves the page arrived; only then does
      // the field's absence mean anything.
      if (await page.locator('records-lwc-highlights-panel, force-highlights2, '
                             + 'records-record-layout-item').count() > 0) {
        await page.waitForTimeout(2500);          // one more pass for a late-rendering field
        return await page.getByText(FIELD_LABEL, { exact: false }).count() > 0;
      }
    } catch (e) { /* a read racing a redirect is a retry, not a failure */ }
    await page.waitForTimeout(1500);
  }
  return null;                                  // never settled — say so, do not guess
};
const shell = async (page) => {
  // Never networkidle on Lightning — it polls forever. Poll for a concrete marker instead.
  for (let i = 0; i < 30; i++) {
    if (await page.locator('one-app-nav-bar').count() > 0) return true;
    await page.waitForTimeout(2000);
  }
  return false;
};

// `headless: true` on its own gets the CHROMIUM HEADLESS SHELL — a stripped build Playwright
// ships alongside the full one, and the likely source of "CSS Error" on Lightning record pages
// when Home and Setup render fine. `channel: 'chromium'` selects the complete browser in new
// headless mode. Both are already installed here; the shell was simply the default.
//
// Overridable so the next person can try 'chrome' against the system install without editing
// this file.
const CHANNEL = process.env.TORQUE_BROWSER_CHANNEL || 'chromium';
const browser = await chromium.launch(
  CHANNEL === 'default' ? { headless: true } : { headless: true, channel: CHANNEL });
mark('browserChannel', CHANNEL);
try {
  // A realistic viewport, because Lightning record pages failed with "Sorry to interrupt / CSS
  // Error" at the headless default — an asset/layout failure, not a permission one. Read out of
  // the dialog body rather than inferred: the header alone says nothing about the cause.
  const page = await browser.newPage({
    viewport: { width: 1680, height: 1050 },
    deviceScaleFactor: 1,
  });

  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 90000 });
  const adminOk = await shell(page);
  mark('adminShell', adminOk);
  mark('adminMs', Date.now() - t0);
  if (!adminOk) { mark('done', 1); process.exit(0); }

  // Baseline: the admin must NOT be denied Setup. Without it, "the impersonated session was
  // denied" could just mean this org denies everyone, and the differential would be measuring
  // nothing — the same vacuous-mutator trap the harness asserts against before every mutation.
  const adminSees = await fieldVisible(page, instanceUrl, RECORD_ID);
  mark('adminFieldVisible', adminSees);

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
  const userSees = await fieldVisible(page, instanceUrl, RECORD_ID);
  mark('asUserFieldVisible', userSees);
  // What did the page ACTUALLY say? Guessing at the wording of a refusal is what produced six
  // runs of regexes; the title and the settled URL are cheap, decisive, and carry no record
  // data. Names and field values are never printed — a diagnostic that leaks PII to explain
  // itself has traded one problem for a worse one.
  try {
    mark('asUserSetupTitle', (await page.title()).slice(0, 70).replace(/\s+/g, ' '));
    mark('asUserSetupUrl', new URL(page.url()).pathname);
    const heads = await page.locator('h1, h2, [role="alert"]').allTextContents();
    mark('asUserSetupHeadings',
         heads.map(h => h.trim()).filter(Boolean).slice(0, 4).join(' | ').slice(0, 120));
  } catch (e) { mark('asUserSetupTitle', 'unavailable'); }

  // ── OBSERVE, rather than hypothesise a fifth selector ──────────────────────────────────
  //
  // Four detector designs have failed and every one was a guess about where the text lives.
  // This reads the accessibility tree, which pierces shadow DOM and returns what is actually
  // rendered, and prints only FIELD-LABEL-SHAPED strings: short, title-case, no digits. A record
  // page is full of customer data, so dumping it to debug a selector would trade a detection
  // problem for a disclosure one.
  if (process.env.TORQUE_DUMP_LABELS === '1') {
    try {
      await page.goto(`${uiHost(instanceUrl)}/lightning/r/Account/${RECORD_ID}/view`,
                      { waitUntil: 'domcontentloaded', timeout: 30000 });
      // 8s was not a settle, it was a guess — and shells alone have taken 5–7s on this org, so
      // the first dump may have photographed a page mid-load and reported "no record" about a
      // record that had not arrived. Poll for record content instead of sleeping on a number.
      for (let i = 0; i < 12; i++) {
        const n = await page.locator('records-lwc-highlights-panel, force-highlights2, '
                                     + 'records-record-layout-item, .slds-page-header__title')
                            .count();
        if (n > 0) break;
        await page.waitForTimeout(2500);
      }
      await page.waitForTimeout(3000);
      // page.accessibility was removed from Playwright; locators are the piercing API that
      // remains. A locator matching elements INSIDE open shadow roots returns their text, which
      // is the whole reason this works where page.content() did not.
      const names = await page.locator(
        'span, dt, label, h1, h2, lightning-formatted-text, .test-id__field-label'
      ).allTextContents();
      const labelish = [...new Set(names.map(s => s.trim()))].filter(s =>
        s.length > 2 && s.length < 34 && !/\d/.test(s) && /^[A-Z]/.test(s));
      // "Sorry to interrupt" is only the HEADER of Salesforce's error dialog; the body says
      // why, and inferring the why from the header is the same guessing this dump replaced.
      try {
        const dlg = await page.locator(
          '[role="dialog"], .slds-modal__container, .errorMessage, .genericError'
        ).allTextContents();
        const body = dlg.map(s => s.replace(/\s+/g, ' ').trim()).filter(Boolean).join(' | ');
        mark('errorDialogText', body.slice(0, 400) || 'no dialog element matched');
      } catch (e) { mark('errorDialogText', 'unavailable'); }
      // WHERE AM I? Never checked, across every variant — and the label count came back
      // identical (57) for the headless shell, full Chromium, system Chrome, and two different
      // hosts. Identical output from four different configurations is not four confirmations,
      // it is a hint that none of them changed what was being looked at.
      mark('dumpUrlHost', new URL(page.url()).host);
      mark('dumpUrlPath', new URL(page.url()).pathname);
      mark('labelCount', labelish.length);
      mark('hasFieldLabel', labelish.some(s => s.includes(FIELD_LABEL)));
      mark('labelSample', labelish.slice(0, 40).join(' / ').slice(0, 700));
    } catch (e) { mark('labelSample', 'unavailable: ' + String(e).slice(0, 80)); }
  }
  mark('fieldVisibilityDifferential',
       adminSees === true && userSees === false ? 'PROVEN'
       : (adminSees === null || userSees === null) ? 'UNSETTLED' : 'ABSENT');
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
