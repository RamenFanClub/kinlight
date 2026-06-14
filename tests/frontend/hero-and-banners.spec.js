const { test, expect } = require('@playwright/test');
const { loginViaUI, setupPage, buildVault, buildFullVault } = require('./helpers');

test.describe('Hero Headline States (F45)', () => {

  test('empty vault shows "Let\'s get you set up."', async ({ page }) => {
    await setupPage(page, { vault: buildVault() });
    await loginViaUI(page);
    await expect(page.locator('#home-hero')).toContainText('set up');
  });

  test('partial vault (30-69%) with no contacts shows "You\'re making progress."', async ({ page }) => {
    // No contacts — F65 does not apply, so progress state fires normally
    const vault = buildVault({
      assets: [{ id: 1, name: 'House', category: 'Property', value: 500000, details: '', beneficiary: 'Jane', notes: '' }],
      wishes: [{ id: 2, category: 'Funeral & Service', title: 'Cremation', details: '', priority: 'high' }],
    });
    await setupPage(page, { vault });
    await loginViaUI(page);
    await expect(page.locator('#home-hero')).toContainText('progress');
  });

  test('partial vault (30-69%) with a contact but no check-in shows amber "Almost there" (F65)', async ({ page }) => {
    // F65: once a contact exists, badge/hero escalates to amber "check in" nudge regardless of completeness %
    const vault = buildVault({
      assets: [{ id: 1, name: 'House', category: 'Property', value: 500000, details: '', beneficiary: 'Jane', notes: '' }],
      wishes: [{ id: 2, category: 'Funeral & Service', title: 'Cremation', details: '', priority: 'high' }],
      kin: [{ id: 3, first: 'Jane', last: 'Doe', rel: 'Partner', email: 'j@t.com', phone: '', notifyVia: 'email', order: 1, letter: '' }],
    });
    await setupPage(page, { vault });
    await loginViaUI(page);
    await expect(page.locator('#home-hero')).toContainText('Almost there');
  });

  test('complete vault without check-in shows amber "Almost there"', async ({ page }) => {
    await setupPage(page, { vault: buildFullVault({ lastCheckin: null }) });
    await loginViaUI(page);
    await expect(page.locator('#home-hero')).toContainText('Almost there');
  });

  test('complete vault with check-in shows "Your light is on."', async ({ page }) => {
    await setupPage(page, { vault: buildFullVault() });
    await loginViaUI(page);
    await expect(page.locator('#home-hero')).toContainText('Your light is on');
  });

  test('overdue vault with contacts shows red "Action needed."', async ({ page }) => {
    const vault = buildFullVault({
      lastCheckin: Date.now() - (90 * 24 * 60 * 60 * 1000),
    });
    await setupPage(page, { vault });
    await loginViaUI(page);
    await expect(page.locator('#home-hero')).toContainText('Action needed');
  });

});

test.describe('Overdue Banner (F01)', () => {

  test('overdue banner is visible when grace period expired', async ({ page }) => {
    const vault = buildFullVault({
      lastCheckin: Date.now() - (90 * 24 * 60 * 60 * 1000),
    });
    await setupPage(page, { vault });
    await loginViaUI(page);
    await expect(page.locator('#overdue-banner')).toBeVisible();
  });

  test('overdue banner is hidden when not overdue', async ({ page }) => {
    await setupPage(page, { vault: buildFullVault() });
    await loginViaUI(page);
    await expect(page.locator('#overdue-banner')).toBeHidden();
  });

  test('overdue banner contains cancellation reassurance (F50)', async ({ page }) => {
    const vault = buildFullVault({
      lastCheckin: Date.now() - (90 * 24 * 60 * 60 * 1000),
    });
    await setupPage(page, { vault });
    await loginViaUI(page);
    const bannerText = await page.locator('#overdue-banner').textContent();
    expect(bannerText).toContain('cancel');
  });

});

test.describe('Reminder Banner (F05)', () => {

  test('reminder banner shows when check-in is due soon', async ({ page }) => {
    // 50 days ago on a 2-month (60-day) window = 10 days left, inside 25% (15-day) amber window
    const vault = buildFullVault({
      lastCheckin: Date.now() - (50 * 24 * 60 * 60 * 1000),
    });
    await setupPage(page, { vault });
    await loginViaUI(page);
    await expect(page.locator('#reminder-banner')).toBeVisible();
  });

  test('reminder banner is hidden when check-in is not due soon', async ({ page }) => {
    await setupPage(page, { vault: buildFullVault() });
    await loginViaUI(page);
    await expect(page.locator('#reminder-banner')).toBeHidden();
  });

});
