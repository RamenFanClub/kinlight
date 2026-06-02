/**
 * F58 — Hero Headline & Banner State Tests
 *
 * Tests the 5-state hero headline (F45), overdue banner (F01),
 * and reminder banner (F05). These are the most visible UI states
 * and the ones most likely to confuse users if they break.
 *
 * KEY CONCEPT: We mock the API to return different vault states,
 * then check what the UI renders. No real backend needed.
 */
const { test, expect } = require('@playwright/test');
const { mockAPI, loginViaUI, clearStorage, buildVault, buildFullVault } = require('./helpers');

test.describe('Hero Headline States (F45)', () => {

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await clearStorage(page);
    await page.reload();
  });

  test('empty vault shows "Let\'s get you set up."', async ({ page }) => {
    // Empty vault = 0% completeness → state 2 (< 30%)
    await mockAPI(page, { vault: buildVault() });
    await loginViaUI(page);

    const hero = page.locator('#home-hero');
    await expect(hero).toContainText('set up');
  });

  test('partial vault (30-69%) shows "You\'re making progress."', async ({ page }) => {
    // 3 of 7 checks = ~43% → state 3
    const vault = buildVault({
      assets: [{ id: 1, name: 'House', category: 'Property', value: 500000, details: '', beneficiary: 'Jane', notes: '' }],
      wishes: [{ id: 2, category: 'Funeral & Service', title: 'Cremation', details: '', priority: 'high' }],
      kin: [{ id: 3, first: 'Jane', last: 'Doe', rel: 'Partner', email: 'j@t.com', phone: '', notifyVia: 'email', order: 1, letter: '' }],
    });
    await mockAPI(page, { vault });
    await loginViaUI(page);

    const hero = page.locator('#home-hero');
    await expect(hero).toContainText('progress');
  });

  test('complete vault without check-in shows amber "Almost there"', async ({ page }) => {
    // All checks pass except lastCheckin → state 4
    const vault = buildFullVault({ lastCheckin: null });
    await mockAPI(page, { vault });
    await loginViaUI(page);

    const hero = page.locator('#home-hero');
    await expect(hero).toContainText('Almost there');
  });

  test('complete vault with check-in shows "Everything is in order."', async ({ page }) => {
    // All 7 checks pass → state 5
    await mockAPI(page, { vault: buildFullVault() });
    await loginViaUI(page);

    const hero = page.locator('#home-hero');
    await expect(hero).toContainText('in order');
  });

  test('overdue vault with contacts shows red "Action needed."', async ({ page }) => {
    // lastCheckin far in the past + grace period expired + has contacts → state 1
    const vault = buildFullVault({
      lastCheckin: Date.now() - (90 * 24 * 60 * 60 * 1000), // 90 days ago (way past 2 months + 7 day grace)
    });
    await mockAPI(page, { vault });
    await loginViaUI(page);

    const hero = page.locator('#home-hero');
    await expect(hero).toContainText('Action needed');
  });

});

test.describe('Overdue Banner (F01)', () => {

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await clearStorage(page);
    await page.reload();
  });

  test('overdue banner is visible when grace period expired', async ({ page }) => {
    const vault = buildFullVault({
      lastCheckin: Date.now() - (90 * 24 * 60 * 60 * 1000),
    });
    await mockAPI(page, { vault });
    await loginViaUI(page);

    await expect(page.locator('#overdue-banner')).toBeVisible();
  });

  test('overdue banner is hidden when not overdue', async ({ page }) => {
    await mockAPI(page, { vault: buildFullVault() });
    await loginViaUI(page);

    await expect(page.locator('#overdue-banner')).toBeHidden();
  });

  test('overdue banner contains cancellation reassurance (F50)', async ({ page }) => {
    const vault = buildFullVault({
      lastCheckin: Date.now() - (90 * 24 * 60 * 60 * 1000),
    });
    await mockAPI(page, { vault });
    await loginViaUI(page);

    // F50: must include "Checking in now will immediately cancel"
    const bannerText = await page.locator('#overdue-banner').textContent();
    expect(bannerText).toContain('cancel');
  });

});

test.describe('Reminder Banner (F05)', () => {

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await clearStorage(page);
    await page.reload();
  });

  test('reminder banner shows when check-in is due soon', async ({ page }) => {
    // 2 months = ~60 days. 25% threshold = 15 days.
    // 50 days ago → 10 days left → inside the 15-day amber window
    const vault = buildFullVault({
      lastCheckin: Date.now() - (50 * 24 * 60 * 60 * 1000),
    });
    await mockAPI(page, { vault });
    await loginViaUI(page);

    await expect(page.locator('#reminder-banner')).toBeVisible();
  });

  test('reminder banner is hidden when check-in is not due soon', async ({ page }) => {
    // Checked in just now → nowhere near due
    await mockAPI(page, { vault: buildFullVault() });
    await loginViaUI(page);

    await expect(page.locator('#reminder-banner')).toBeHidden();
  });

});
