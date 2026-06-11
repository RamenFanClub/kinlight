/**
 * F58 — Settings Tests
 */
const { test, expect } = require('@playwright/test');
const { loginViaUI, setupPage, buildVault } = require('./helpers');

test.describe('Settings', () => {

  test.beforeEach(async ({ page }) => {
    await setupPage(page, { vault: buildVault() });
    await loginViaUI(page);
    await page.click('#n-config');
  });

  test('check-in frequency defaults to 2 months', async ({ page }) => {
    await expect(page.locator('#fc')).toHaveText('2');
    await expect(page.locator('#fu')).toHaveText('Months');
  });

  test('can increase check-in frequency', async ({ page }) => {
    // adj(1) is the + button — use onclick attribute to target the right button
    await page.locator('[onclick="adj(1)"]').click();
    await expect(page.locator('#fc')).toHaveText('3');
  });

  test('can decrease check-in frequency', async ({ page }) => {
    await page.locator('[onclick="adj(-1)"]').click();
    await expect(page.locator('#fc')).toHaveText('1');
  });

  test('can switch frequency unit to Weeks', async ({ page }) => {
    await page.click('#tw');
    await expect(page.locator('#tw')).toHaveClass(/on/);
    await expect(page.locator('#fu')).toHaveText('Weeks');
  });

  test('grace period defaults to 7 days (F56)', async ({ page }) => {
    await expect(page.locator('#gp-val')).toHaveText('7');
  });

  test('can increase grace period', async ({ page }) => {
    // Grace period buttons call adjGP(1) / adjGP(-1)
    await page.locator('[onclick="adjGP(1)"]').click();
    await expect(page.locator('#gp-val')).toHaveText('8');
  });

  test('can decrease grace period', async ({ page }) => {
    await page.locator('[onclick="adjGP(-1)"]').click();
    await expect(page.locator('#gp-val')).toHaveText('6');
  });

  test('notification protocol options are in plain English (F49)', async ({ page }) => {
    const text = await page.locator('#s-config').textContent();
    expect(text).toContain('Wait 3 extra days');
    expect(text).toContain('Notify contacts immediately');
    expect(text).toContain('one at a time');
  });

  test('frequency changes persist to localStorage', async ({ page }) => {
    await page.locator('[onclick="adj(1)"]').click(); // increase to 3
    await page.waitForTimeout(300); // allow save() to run
    const state = await page.evaluate(() => JSON.parse(localStorage.getItem('ee_v3') || '{}'));
    expect(state.fc).toBe(3);
  });

});
