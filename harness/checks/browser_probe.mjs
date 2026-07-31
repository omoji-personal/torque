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
  console.log('RENDER_FAIL nav bar absent after settle; url=' + page.url().slice(0, 60));
  process.exit(1);
} catch (e) {
  console.log('RENDER_FAIL ' + String(e).slice(0, 80));
  process.exit(1);
} finally { await browser.close(); }
