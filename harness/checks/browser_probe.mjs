// Real render probe (matches the proven diagnostic path): navigate the frontdoor URL,
// let the redirect chain + Lightning shell settle, then assert the nav bar is present.
// waitForSelector races the frontdoor→lightning redirect on cold headless starts, so we
// poll a settle window instead. Token is read from a 0600 file (argv[2]), never argv.
import { chromium } from 'playwright';
import { readFileSync, unlinkSync } from 'fs';
const urlFile = process.argv[2];
const url = readFileSync(urlFile, 'utf8').trim();
try { unlinkSync(urlFile); } catch {}
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 90000 });
  // Lightning boots after the redirect; poll for the shell marker up to 90s.
  let count = 0;
  for (let i = 0; i < 30; i++) {
    await page.waitForTimeout(3000);
    count = await page.locator('one-app-nav-bar').count();
    if (count > 0) break;
  }
  if (count > 0) { console.log('RENDER_OK ' + (await page.title()).slice(0, 40)); process.exit(0); }
  // NAME WHERE IT STOPPED. This printed page.url().slice(0, 60), which truncated mid-host to
  // "...develop.my." and threw away the only fact that identified the problem — and slicing a
  // raw URL is also how a session token escapes into a log. host+pathname carries no query.
  //
  // Measured 2026-08-06: the frontdoor session was fine and the chain ended at
  // /_ui/system/security/ChangePassword — an EXPIRED PASSWORD. Lightning never boots because the
  // org never lets you past the password screen, and "nav bar absent" reads as a render fault.
  // Four hours and eight ruled-out hypotheses went into a Lightning bug that was not there.
  let where = '(unparseable)';
  try { const u = new URL(page.url()); where = u.host + u.pathname; } catch {}
  let hint = '';
  if (where.includes('/_ui/system/security/ChangePassword')) {
    hint = ' — the org is forcing a password change, so NO Lightning page can load. Reset the '
         + 'password, and set the profile policy to never expire so it does not recur.';
  } else if (where.includes('/login') || where.includes('/secur/')) {
    hint = ' — still inside the auth chain; the session handoff did not complete.';
  }
  console.log('RENDER_FAIL nav bar absent after settle; landed=' + where + hint);
  process.exit(1);
} catch (e) {
  console.log('RENDER_FAIL ' + String(e).slice(0, 80));
  process.exit(1);
} finally { await browser.close(); }
