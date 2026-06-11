/**
 * F58 — Notification Queue & Protocol Tests
 * Tests the notification queue card (F01) and protocol rendering.
 */
const { test, expect } = require('@playwright/test');
const { loginViaUI, setupPage, buildFullVault } = require('./helpers');

test.describe('Notification Queue (F01)', () => {

  const overdueVault = () => buildFullVault({
    lastCheckin: Date.now() - (90 * 24 * 60 * 60 * 1000),
    notifyProto: 'ping_then_notify',
  });

  test('notification queue card visible when overdue', async ({ page }) => {
    await setupPage(page, { vault: overdueVault() });
    await loginViaUI(page);
    await expect(page.locator('#home-nq')).toBeVisible();
  });

  test('notification queue card hidden when not overdue', async ({ page }) => {
    await setupPage(page, { vault: buildFullVault() });
    await loginViaUI(page);
    await expect(page.locator('#home-nq')).toBeHidden();
  });

  test('notification queue shows contact name when overdue', async ({ page }) => {
    await setupPage(page, { vault: overdueVault() });
    await loginViaUI(page);
    const queueText = await page.locator('#home-nq').textContent();
    expect(queueText).toContain('Jane');
  });

  test('can open notification queue modal', async ({ page }) => {
    await setupPage(page, { vault: overdueVault() });
    await loginViaUI(page);
    // Click "View details" or the NQ card to open modal
    const nqCard = page.locator('#home-nq');
    await nqCard.locator('button, [onclick*="nqm"]').first().click();
    await expect(page.locator('#nqm')).toHaveClass(/on/);
  });

  test('notification queue modal shows active protocol label (F49)', async ({ page }) => {
    await setupPage(page, { vault: overdueVault() });
    await loginViaUI(page);
    await page.locator('#home-nq button, #home-nq [onclick*="nqm"]').first().click();
    const text = await page.locator('#nq-proto-label').textContent();
    expect(text).toContain('Wait 3 extra days');
  });

});
