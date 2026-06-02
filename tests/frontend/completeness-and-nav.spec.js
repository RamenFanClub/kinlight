/**
 * F58 — Completeness Score & Navigation Tests
 *
 * Tests the 7-check completeness scoring system (rendered on Home)
 * and navigation between the app's 5 screens.
 *
 * COMPLETENESS CHECKS (each worth ~14.3%):
 * 1. At least one asset
 * 2. At least one asset has a beneficiary
 * 3. At least one wish
 * 4. Will details recorded
 * 5. Statement of Wishes in suppDocs
 * 6. At least one contact
 * 7. First check-in completed
 */
const { test, expect } = require('@playwright/test');
const { mockAPI, loginViaUI, clearStorage, buildVault, buildFullVault } = require('./helpers');

test.describe('Completeness Score', () => {

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await clearStorage(page);
    await page.reload();
  });

  test('empty vault shows 0%', async ({ page }) => {
    await mockAPI(page, { vault: buildVault() });
    await loginViaUI(page);

    const label = page.locator('#cp-lbl');
    await expect(label).toHaveText('0%');
  });

  test('full vault shows 100%', async ({ page }) => {
    await mockAPI(page, { vault: buildFullVault() });
    await loginViaUI(page);

    const label = page.locator('#cp-lbl');
    await expect(label).toHaveText('100%');
  });

  test('partial vault shows correct percentage', async ({ page }) => {
    // 3 of 7 checks: asset, asset with beneficiary, contact → 43%
    const vault = buildVault({
      assets: [{ id: 1, name: 'House', category: 'Property', value: 500000, details: '', beneficiary: 'Jane', notes: '' }],
      kin: [{ id: 2, first: 'Jane', last: 'Doe', rel: 'Partner', email: 'j@t.com', phone: '', notifyVia: 'email', order: 1, letter: '' }],
    });
    await mockAPI(page, { vault });
    await loginViaUI(page);

    const label = page.locator('#cp-lbl');
    await expect(label).toHaveText('43%');
  });

  test('progress bar width matches percentage', async ({ page }) => {
    await mockAPI(page, { vault: buildFullVault() });
    await loginViaUI(page);

    const bar = page.locator('#cp-bar');
    // style.width should be "100%"
    await expect(bar).toHaveCSS('width', /.+/);
    const width = await bar.evaluate((el) => el.style.width);
    expect(width).toBe('100%');
  });

  test('completeness tips show for incomplete vault', async ({ page }) => {
    await mockAPI(page, { vault: buildVault() });
    await loginViaUI(page);

    // Tips section should have content (actionable suggestions)
    const tips = page.locator('#cp-tips');
    const text = await tips.textContent();
    expect(text.length).toBeGreaterThan(0);
  });

});

test.describe('Navigation', () => {

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await clearStorage(page);
    await page.reload();
    await mockAPI(page, { vault: buildVault() });
    await loginViaUI(page);
  });

  test('Home screen is visible by default after login', async ({ page }) => {
    await expect(page.locator('#s-home')).toBeVisible();
  });

  test('can navigate to Assets screen', async ({ page }) => {
    await page.click('#n-ledger');
    await expect(page.locator('#s-ledger')).toBeVisible();
    await expect(page.locator('#s-home')).toBeHidden();
  });

  test('can navigate to Wishes screen', async ({ page }) => {
    await page.click('#n-wishes');
    await expect(page.locator('#s-wishes')).toBeVisible();
    await expect(page.locator('#s-home')).toBeHidden();
  });

  test('can navigate to Contacts screen', async ({ page }) => {
    await page.click('#n-contacts');
    await expect(page.locator('#s-contacts')).toBeVisible();
    await expect(page.locator('#s-home')).toBeHidden();
  });

  test('can navigate to Settings screen', async ({ page }) => {
    await page.click('#n-settings');
    await expect(page.locator('#s-settings')).toBeVisible();
    await expect(page.locator('#s-home')).toBeHidden();
  });

  test('can navigate back to Home from another screen', async ({ page }) => {
    await page.click('#n-settings');
    await expect(page.locator('#s-settings')).toBeVisible();

    await page.click('#n-home');
    await expect(page.locator('#s-home')).toBeVisible();
    await expect(page.locator('#s-settings')).toBeHidden();
  });

});
