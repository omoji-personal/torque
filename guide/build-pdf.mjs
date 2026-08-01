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
    <div style="width:100%; font-family:'Andale Mono',Menlo,monospace; font-size:7.6pt;
                color:#5B6672; letter-spacing:0.06em; padding:0 0.8in;
                display:flex; justify-content:space-between; align-items:center;">
      <span>TORQUE</span>
      <span style="color:#6C747D;">github.com/omoji-personal/torque</span>
      <span class="pageNumber"></span>
    </div>`,
  // Page geometry lives HERE, not in @page — preferCSSPageSize:false means these win, and two
  // disagreeing sources would mean Cmd-P on the HTML produced a different document than the
  // shipped PDF. The @page rule was removed from the stylesheet rather than left to rot.
  margin: { top: '0.68in', bottom: '0.72in', left: '0.8in', right: '0.8in' },
  preferCSSPageSize: false,
});

await browser.close();

// Chromium stamps /Author=Chromium. Fix the attribution before anyone reads it as authorship.
// Non-fatal: polish must never be able to break the deliverable.
try {
  const { spawnSync } = await import('child_process');
  const r = spawnSync('python3', [join(here, 'stamp-metadata.py'), out], { encoding: 'utf8' });
  if (r.stdout) process.stdout.write(r.stdout);
  if (r.stderr) process.stderr.write(r.stderr);
} catch (e) {
  console.warn('  · metadata stamp skipped:', e.message);
}

const kb = (statSync(out).size / 1024).toFixed(0);
console.log(`built ${out} (${kb} KB)`);
