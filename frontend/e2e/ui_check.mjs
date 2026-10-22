/* UI regression: no duplicated assistant text after completion.
 * Sends a text-only prompt, waits for done, counts the marker in
 * assistant bubbles — must be exactly 1 (stream text cleared on terminal).
 */
import { chromium } from 'playwright';

const BASE = process.env.ATLAS_E2E_BASE || 'http://127.0.0.1:8765';
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
let ok = true;
try {
  await page.goto(BASE, { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForSelector('.brand-name', { timeout: 15000 });
  await page.click('text=New chat');
  await page.waitForSelector('.chead-title', { timeout: 10000 });
  page.on('dialog', (d) => d.dismiss());
  // Wait until the model catalog has loaded into the picker.
  await page.waitForFunction(
    () => !document.querySelector('.model-btn')?.textContent?.includes('no model'),
    { timeout: 30000 });
  await page.fill('.composer-box textarea', 'Reply with exactly: UI-OK and nothing else.');
  await page.waitForFunction(
    () => !document.querySelector('.composer-box .btn.primary')?.hasAttribute('disabled'),
    { timeout: 15000 });
  await page.click('.composer-box .btn.primary');
  // Wait for the assistant answer first (avoids racing a fast completion),
  // then for the run to finish.
  await page.waitForSelector('.msg.assistant', { timeout: 300000 });
  await page.waitForFunction(() => !document.querySelector('.status-pill'), { timeout: 300000 });
  const assistantTexts = await page.locator('.msg.assistant .bubble').allInnerTexts();
  const hits = assistantTexts.filter((t) => t.includes('UI-OK')).length;
  console.log(`assistant bubbles containing UI-OK: ${hits} (expect 1)`);
  if (hits !== 1) ok = false;
  // No stale streaming caret after completion.
  const carets = await page.locator('.caret').count();
  console.log(`streaming carets after completion: ${carets} (expect 0)`);
  if (carets !== 0) ok = false;
  // Activity pill must have auto-dismissed (5s) — wait a bit then check.
  await page.waitForTimeout(7000);
  const pills = await page.locator('.activity-pill').count();
  console.log(`activity pills after dismiss window: ${pills} (expect 0)`);
  if (pills !== 0) ok = false;
  if (errors.length) { console.log('page errors:', errors.join(' | ')); ok = false; }
} catch (e) {
  console.log('FAILED:', String(e).slice(0, 300));
  ok = false;
} finally {
  await browser.close();
}
console.log(ok ? 'UI CHECK PASS' : 'UI CHECK FAIL');
process.exit(ok ? 0 : 1);
