/* Boot check: page loads against the current dist with no console/page errors. */
import { chromium } from 'playwright';

const BASE = process.env.ATLAS_E2E_BASE || 'http://127.0.0.1:8765';
const browser = await chromium.launch();
const page = await browser.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
await page.goto(BASE, { waitUntil: 'networkidle', timeout: 30000 });
await page.waitForSelector('.brand-name', { timeout: 15000 });
await page.waitForSelector('.convlist, .empty-card', { timeout: 15000 });
console.log(errors.length === 0 ? 'BOOT OK' : `BOOT ERRORS: ${errors.join(' | ')}`);
await browser.close();
process.exit(errors.length ? 1 : 0);
