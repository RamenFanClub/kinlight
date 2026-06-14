/**
 * F58 — Activity Log + Summary Tests
 * Recent activity log on Home, asset/wish summary counts.
 */
const { test, expect } = require('@playwright/test');
const { loginViaUI, setupPage, buildVault, buildFullVault } = require('./helpers');

test.describe('Home Summary & Activity Log', () => {

  test('summary shows "0 assets · 0 wishes" on empty vault', async ({ page }) => {
    await setupPage(page, { vault: buildVault() });
    await loginViaUI(page);
    await expect(page.locator('#d-sum')).toContainText('0 assets');
    await expect(page.locator('#d-sum')).toContainText('0 wishes');
  });

  test('summary count updates when assets added', async ({ page }) => {
    await setupPage(page, {
      vault: buildVault({
        assets: [
          { id: 1, name: 'House', category: 'Property', value: 500000, details: '', beneficiary: '', notes: '' },
          { id: 2, name: 'Car', category: 'Vehicle', value: 20000, details: '', beneficiary: '', notes: '' },
        ]
      })
    });
    await loginViaUI(page);
    await expect(page.locator('#d-sum')).toContainText('2 assets');
  });

  test('status badge shows "Light on" for normal vault', async ({ page }) => {
    await setupPage(page, { vault: buildFullVault() });
    await loginViaUI(page);
    await expect(page.locator('#status-badge')).toContainText('Light on');
  });

  test('status badge shows "Overdue" when overdue', async ({ page }) => {
    await setupPage(page, {
      vault: buildFullVault({ lastCheckin: Date.now() - (90 * 24 * 60 * 60 * 1000) })
    });
    await loginViaUI(page);
    await expect(page.locator('#status-badge')).toContainText('Overdue');
  });

  test('status badge shows "Due Soon" when reminder active', async ({ page }) => {
    await setupPage(page, {
      vault: buildFullVault({ lastCheckin: Date.now() - (50 * 24 * 60 * 60 * 1000) })
    });
    await loginViaUI(page);
    await expect(page.locator('#status-badge')).toContainText('Due Soon');
  });

  test('activity log shows "No activity yet" on empty vault', async ({ page }) => {
    await setupPage(page, { vault: buildVault() });
    await loginViaUI(page);
    await expect(page.locator('#d-act')).toContainText('No activity yet');
  });

  test('activity log shows entries after adding an asset', async ({ page }) => {
    await setupPage(page, { vault: buildVault() });
    await loginViaUI(page);
    await page.click('#n-ledger');
    await page.locator('#s-ledger .gbtn').click();
    await page.fill('#an', 'Test Asset');
    await page.locator('#am').getByText('Save Asset').click();
    await page.click('#n-home');
    const logText = await page.locator('#d-act').textContent();
    expect(logText).not.toContain('No activity yet');
  });

});
