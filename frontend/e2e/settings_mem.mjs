/* Settings memory tab: SOUL.md renders, edit/save/rollback round-trip works. */
import { chromium } from 'playwright';

const BASE = process.env.ATLAS_E2E_BASE || 'http://127.0.0.1:8765';
const browser = await chromium.launch();
const page = await browser.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
let ok = true;
try {
  await page.goto(BASE, { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForSelector('.brand-name', { timeout: 15000 });
  await page.click('text=Settings', { timeout: 10000 }).catch(() => page.locator('.sidebar-foot .btn').click());
  await page.waitForSelector('.settings-modal', { timeout: 10000 });
  await page.click('.settings-tabs .mem-tab:text("SOUL.md")');
  await page.waitForSelector('.memfile-panel', { timeout: 10000 });
  const text = await page.locator('.memfile-panel .mem-file').innerText();
  console.log('soul renders:', text.includes('Atlas') || text.includes('principles'));
  if (!/atlas|principle/i.test(text)) ok = false;
  // edit + save round trip
  await page.click('.memfile-panel button:text("Edit")');
  await page.fill('.mem-editor', (await page.locator('.mem-editor').inputValue()) + '\n- Test line settings-ui.\n');
  await page.click('.memfile-panel button:text("Save")');
  await page.waitForSelector('.mem-file', { timeout: 10000 });
  const after = await page.locator('.memfile-panel .mem-file').innerText();
  console.log('saved line visible:', after.includes('Test line settings-ui'));
  if (!after.includes('Test line settings-ui')) ok = false;
  // revert via rollback (restores pre-test version)
  await page.click('.history-toggle');
  const count = await page.locator('.mem-history .mem-version').count();
  console.log('history entries:', count);
  if (count < 2) ok = false;
  await page.locator('.mem-history .mem-version').nth(1).locator('button').click();
  await page.waitForTimeout(1500);
  const reverted = await page.locator('.memfile-panel .mem-file').innerText();
  console.log('rollback removed test line:', !reverted.includes('Test line settings-ui'));
  if (reverted.includes('Test line settings-ui')) ok = false;
  if (errors.length) { console.log('page errors:', errors.join(' | ')); ok = false; }
} catch (e) {
  console.log('FAILED:', String(e).slice(0, 400));
  ok = false;
} finally {
  await browser.close();
}
console.log(ok ? 'SETTINGS MEM UI PASS' : 'SETTINGS MEM UI FAIL');
process.exit(ok ? 0 : 1);
