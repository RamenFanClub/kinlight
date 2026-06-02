/**
 * F58 — Copy & Label Verification Tests
 *
 * Tests that shipped copy changes (F49, F53, F54, F56) are actually
 * present in the UI. These are "canary" tests — they catch regressions
 * where someone accidentally reverts copy to old versions.
 *
 * Each test logs in, navigates to the relevant screen, and checks
 * that the correct text is rendered.
 */
const { test, expect } = require('@playwright/test');
const { mockAPI, loginViaUI, clearStorage, buildVault } = require('./helpers');

test.describe('Copy & Label Verification', () => {

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await clearStorage(page);
    await page.reload();
    await mockAPI(page, { vault: buildVault() });
    await loginViaUI(page);
  });

  test('Assets screen title says "My Assets" not "Asset Ledger" (F53)', async ({ page }) => {
    await page.click('#n-ledger');

    // The screen title should say "My Assets"
    const title = page.locator('#s-ledger .st');
    await expect(title).toHaveText('My Assets');
  });

  test('Nav label says "Assets" not "Ledger" (F53)', async ({ page }) => {
    // The bottom nav label for the ledger tab
    const navLabel = page.locator('#n-ledger .nl');
    await expect(navLabel).toHaveText('Assets');
  });

  test('Wishes screen CTA says "Add a Wish" not "New Instruction" (F54)', async ({ page }) => {
    await page.click('#n-wishes');

    // Look for the main CTA button text
    const cta = page.locator('#s-wishes .gc');
    const ctaText = await cta.textContent();
    expect(ctaText).toContain('Add a Wish');
  });

  test('Settings shows grace period default of 7 days (F56)', async ({ page }) => {
    await page.click('#n-settings');

    // The grace period display should show 7
    const gpValue = page.locator('#gp-val');
    await expect(gpValue).toHaveText('7');
  });

  test('Privacy note mentions cloud storage (F31)', async ({ page }) => {
    // Privacy note should be on Home screen
    const privacyNote = page.locator('#privacy-note');
    const text = await privacyNote.textContent();
    expect(text).toContain('cloud');
  });

  test('Contacts screen info box describes what contacts receive (F46)', async ({ page }) => {
    await page.click('#n-contacts');

    // Info box should mention the full package
    const infoBox = page.locator('#s-contacts .info-box, #s-contacts .sage-info');
    const text = await infoBox.first().textContent();
    expect(text).toContain('full package');
  });

});
