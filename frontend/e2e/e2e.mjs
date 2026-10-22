/* Browser E2E (Playwright + bun): boots the real app served by the backend,
 * creates a conversation, switches model to the local coder, sends a trivial
 * prompt, and verifies streaming completion + refresh recovery.
 * Run: bun e2e/e2e.mjs  (backend must be up on 8765)
 */
import { chromium } from 'playwright';

const BASE = process.env.ATLAS_E2E_BASE || 'http://127.0.0.1:8765';
const results = [];
const check = (name, ok, extra = '') => {
  results.push([ok ? 'PASS' : 'FAIL', name, extra]);
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name} ${extra}`);
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));

try {
  await page.goto(BASE, { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForSelector('.brand-name', { timeout: 15000 });
  check('app boots with brand', await page.locator('.brand-name').isVisible());

  // New conversation via sidebar button.
  await page.click('text=New chat');
  await page.waitForSelector('.chead-title', { timeout: 10000 });
  check('new conversation creates header', true);

  // Model picker lists real catalog models.
  await page.click('.model-btn');
  await page.waitForSelector('.picker-pop', { timeout: 10000 });
  const pickerText = await page.locator('.picker-pop').innerText();
  check('model picker lists ollama models', pickerText.includes('ollama/'),
    pickerText.slice(0, 120));
  // Pick the small coder model for the live send.
  const target = page.locator('.picker-item', { hasText: 'qwen2.5-coder:1.5b' });
  if (await target.count()) {
    await target.first().click();
    check('model switch selects coder', true);
  } else {
    await page.keyboard.press('Escape');
    check('model switch selects coder', false, 'coder model missing from catalog');
  }

  // Send a trivial text-only prompt; the 1.5b model answers without tools.
  await page.fill('.composer-box textarea', 'Reply with exactly: E2E-OK and nothing else.');
  await page.click('.composer-box .btn.primary');
  // Streaming caret or assistant text should appear.
  await page.waitForSelector('.msg.streaming, .msg.assistant', { timeout: 120000 });
  check('streaming response appears', true);
  // Wait for execution pill to clear (completion) — generous for CPU inference.
  await page.waitForFunction(
    () => !document.querySelector('.status-pill'), { timeout: 300000 });
  const bodyText = await page.locator('.msglist').innerText();
  check('assistant answer persisted in DOM', bodyText.includes('E2E-OK'), bodyText.slice(-200));

  // Refresh recovery: same conversation + message visible after reload.
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForSelector('.msglist', { timeout: 15000 });
  const after = await page.locator('.msglist').innerText();
  check('refresh restores conversation state', after.includes('E2E-OK'));

  check('no page errors', errors.length === 0, errors.slice(0, 3).join(' | '));
} catch (e) {
  check('e2e flow', false, String(e).slice(0, 300));
} finally {
  await browser.close();
}
const failed = results.filter(([s]) => s === 'FAIL');
console.log(`\nE2E: ${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
