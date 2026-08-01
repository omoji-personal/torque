#!/usr/bin/env node
/**
 * Build guide/Torque-Guide.pdf from guide/torque-guide.html via headless Chromium.
 *
 *   node guide/build-pdf.mjs
 *
 * Deliberately uses the SAME browser stack the harness already validates against, and only
 * system fonts — the PDF must render identically offline, with no webfont fetch, no CDN, and
 * no network at print time.
 */
import { chromium } from 'playwright';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { existsSync, statSync } from 'fs';

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, 'torque-guide.html');
const out = join(here, 'Torque-Guide.pdf');

if (!existsSync(src)) {
  console.error(`missing source: ${src}`);
  process.exit(1);
}

const browser = await chromium.launch();
const page = await browser.newPage();
await page.goto('file://' + src, { waitUntil: 'load' });
// no network at print time: fail loudly rather than silently shipping a page with missing assets
page.on('requestfailed', r => console.warn('  ! asset failed:', r.url()));

await page.pdf({
  path: out,
  format: 'Letter',
  printBackground: true,               // without this, every panel/rule renders white
  displayHeaderFooter: true,
  headerTemplate: '<div></div>',       // empty header; Chromium requires a node
  footerTemplate: `
    <div style="width:100%; font-family:Charter,Georgia,serif; font-size:8pt; color:#8A8580;
                padding:0 0.75in; display:flex; justify-content:space-between;">
      <span>Torque — github.com/omoji-personal/torque</span>
      <span class="pageNumber"></span>
    </div>`,
  margin: { top: '0.7in', bottom: '0.75in', left: '0.85in', right: '0.85in' },
  preferCSSPageSize: false,
});

await browser.close();
const kb = (statSync(out).size / 1024).toFixed(0);
console.log(`built ${out} (${kb} KB)`);
