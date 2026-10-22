/* Notes + Tasks UI smoke: sidebar buttons, views render, create note+task via UI, no page errors. */
import { chromium } from 'playwright';

const BASE = process.env.ATLAS_E2E_BASE || 'http://127.0.0.1:8765';
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1500, height: 900 } });
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
let ok = true;
try {
  await page.goto(BASE, { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForSelector('.brand-name', { timeout: 15000 });

  // Notes view ($eval click: bypasses actionability flakiness in the huge sidebar)
  await page.waitForSelector('aside .btn.newchat:has-text("Notes")', { timeout: 15000 });
  await page.$eval('aside .btn.newchat:has-text("Notes")', (el) => el.click());
  await page.waitForSelector('input[placeholder="new/note.md"]', { timeout: 15000 });
  const stamp = Date.now().toString(36);
  await page.fill('input[placeholder="new/note.md"]', `UI/Smoke-${stamp}.md`);
  await page.keyboard.press('Enter');
  await page.waitForFunction(
    (s) => document.body.innerText.includes(s),
    `Smoke-${stamp}`,
    { timeout: 15000 },
  );
  console.log('notes view renders + note created');

  // Tasks view
  await page.waitForSelector('aside .btn.newchat:has-text("Tasks")', { timeout: 15000 });
  await page.$eval('aside .btn.newchat:has-text("Tasks")', (el) => el.click());
  await page.waitForSelector('input[placeholder*="New task"]', { timeout: 15000 });
  await page.fill('input[placeholder*="New task"]', `UI smoke task ${stamp}`);
  await page.keyboard.press('Enter');
  await page.waitForFunction(
    (s) => document.body.innerText.includes(s),
    `UI smoke task ${stamp}`,
    { timeout: 15000 },
  );
  console.log('tasks view renders + task created');

  // Notification bell present
  const bell = await page.locator('aside .icon-btn[title="Notifications"]').count();
  console.log('notification bell:', bell);
  if (bell !== 1) ok = false;

  if (errors.length) { console.log('page errors:', errors.slice(0, 5).join(' | ')); ok = false; }
} catch (e) {
  console.log('FAILED:', String(e).slice(0, 400));
  ok = false;
} finally {
  await browser.close();
}
console.log(ok ? 'NOTES/TASKS UI PASS' : 'NOTES/TASKS UI FAIL');
process.exit(ok ? 0 : 1);
