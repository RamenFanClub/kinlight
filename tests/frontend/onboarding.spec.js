/**
 * F58 — Onboarding & Milestone Tests
 *
 * Tests the F44 explainer card (first-run experience) and
 * F51 first check-in milestone overlay.
 *
 * These features rely on localStorage flags:
 * - ee_onboarded: controls whether the explainer card shows
 * - ee_first_checkin_done: controls milestone overlay + pulse explainer
 */
const { test, expect } = require('@playwright/test');
const { mockAPI, loginViaUI, clearStorage, buildVault } = require('./helpers');

test.describe('First-Run Explainer Card (F44)', () => {

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await clearStorage(page);
    await page.reload();
  });

  test('explainer card shows on first visit (no ee_onboarded flag)', async ({ page }) => {
    await mockAPI(page, { vault: buildVault() });
    await loginViaUI(page);

    // The explainer card should be visible
    const explainer = page.locator('#explainer-card');
    await expect(explainer).toBeVisible();
  });

  test('explainer card is hidden when ee_onboarded is set', async ({ page }) => {
    // Simulate a returning user who has already dismissed the card
    await mockAPI(page, { vault: buildVault() });

    // Set the flag BEFORE login so it's present when render() runs
    await page.evaluate(() => localStorage.setItem('ee_onboarded', 'true'));
    await loginViaUI(page);

    const explainer = page.locator('#explainer-card');
    await expect(explainer).toBeHidden();
  });

  test('dismissing explainer sets ee_onboarded flag', async ({ page }) => {
    await mockAPI(page, { vault: buildVault() });
    await loginViaUI(page);

    // Click the dismiss button
    const dismissBtn = page.locator('.ex-dismiss');
    await dismissBtn.click();

    // Flag should now be set
    const flag = await page.evaluate(() => localStorage.getItem('ee_onboarded'));
    expect(flag).toBe('true');
  });

  test('explainer stays hidden after page reload', async ({ page }) => {
    await mockAPI(page, { vault: buildVault() });
    await loginViaUI(page);

    // Dismiss it
    await page.locator('.ex-dismiss').click();

    // Reload the page (re-mock API since routes reset)
    await mockAPI(page, { vault: buildVault() });
    await page.reload();

    // Need to re-login after reload (sessionStorage persists within session)
    // Actually sessionStorage survives reload, so showApp should auto-fire
    // Wait for the app to render
    await page.waitForSelector('#home-hero', { timeout: 5000 });

    // Explainer should still be hidden
    const explainer = page.locator('#explainer-card');
    await expect(explainer).toBeHidden();
  });

});

test.describe('Pulse Card Explainer (F48)', () => {

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await clearStorage(page);
    await page.reload();
  });

  test('pulse explainer text shows before first check-in', async ({ page }) => {
    await mockAPI(page, { vault: buildVault() });
    await loginViaUI(page);

    const pulseExplainer = page.locator('#pulse-explainer');
    await expect(pulseExplainer).toBeVisible();
    const text = await pulseExplainer.textContent();
    expect(text).toContain('Check in regularly');
  });

  test('pulse explainer is hidden after first check-in', async ({ page }) => {
    await mockAPI(page, { vault: buildVault() });
    // Set the flag that indicates first check-in is done
    await page.evaluate(() => localStorage.setItem('ee_first_checkin_done', 'true'));
    await loginViaUI(page);

    const pulseExplainer = page.locator('#pulse-explainer');
    await expect(pulseExplainer).toHaveClass(/hidden/);
  });

});
