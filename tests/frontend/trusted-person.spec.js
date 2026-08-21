/**
 * F115 — Trusted Person (delegated responder)
 */
const { test, expect } = require('@playwright/test');
const { API_BASE, loginViaUI, setupPage, buildVault } = require('./helpers');

function vaultWithTrusted(overrides = {}) {
  return buildVault({
    kin: [
      { id: 1, first: 'Jane', last: 'Doe', rel: 'Partner', email: 'jane@test.com', phone: '', notifyVia: 'email', order: 1, letter: '', isTrusted: true },
      { id: 2, first: 'Sam', last: 'Roe', rel: 'Sibling', email: 'sam@test.com', phone: '', notifyVia: 'email', order: 2, letter: '' },
    ],
    trustedEnabled: true,
    ...overrides,
  });
}

test.describe('Trusted person — contact designation', () => {

  test.beforeEach(async ({ page }) => {
    await setupPage(page, { vault: vaultWithTrusted() });
    await loginViaUI(page);
    await page.click('#n-kin');
  });

  test('trusted contact shows a badge on the card', async ({ page }) => {
    await expect(page.locator('#k-list')).toContainText('Trusted person — notified first');
  });

  test('editing a contact pre-checks the trusted box', async ({ page }) => {
    await page.locator('[onclick="editK(1)"]').click();
    await expect(page.locator('#k-trusted')).toBeChecked();
    await expect(page.locator('#km-title')).toHaveText('Edit Contact');
  });

  test('designating a new trusted contact clears the previous one', async ({ page }) => {
    // Open edit on the non-trusted contact and tick trusted
    await page.locator('[onclick="editK(2)"]').click();
    await page.check('#k-trusted');
    await page.click('#km-save-btn');
    await page.waitForTimeout(300);
    const state = await page.evaluate(() => JSON.parse(localStorage.getItem('ee_v3') || '{}'));
    const trusted = state.kin.filter((k) => k.isTrusted);
    expect(trusted.length).toBe(1);
    expect(trusted[0].id).toBe(2);
  });
});

test.describe('Trusted person — settings', () => {

  test.beforeEach(async ({ page }) => {
    await setupPage(page, { vault: vaultWithTrusted() });
    await loginViaUI(page);
    await page.click('#n-config');
  });

  test('shows trusted contact name when enabled', async ({ page }) => {
    await expect(page.locator('#trusted-label')).toContainText('Jane');
  });

  test('test-link button visible when enabled and trusted contact exists', async ({ page }) => {
    await expect(page.locator('#trusted-test-btn')).toBeVisible();
  });

  test('can disable trusted person', async ({ page }) => {
    await page.click('#trusted-toggle-btn');
    await page.waitForTimeout(300);
    const state = await page.evaluate(() => JSON.parse(localStorage.getItem('ee_v3') || '{}'));
    expect(state.trustedEnabled).toBe(false);
  });
});

test.describe('Trusted person — read-only view', () => {

  test('renders read-only vault from magic link and can resolve', async ({ page }) => {
    const holderName = 'Test Holder';
    const vault = vaultWithTrusted({
      assets: [{ id: 3, name: 'House', category: 'Property', value: 500000, details: '', beneficiary: 'Jane', notes: '' }],
    });

    await page.goto('/');
    await page.evaluate(() => { localStorage.clear(); sessionStorage.clear(); });

    await page.route(`${API_BASE}/trusted/access?token=magic`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, token: 'scoped-jwt', holderName }) });
    });
    await page.route(`${API_BASE}/trusted/vault`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, vault }) });
    });
    await page.route(`${API_BASE}/trusted/resolve`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true,"resolved":true}' });
    });

    await page.goto('/?trusted=magic');

    await expect(page.locator('#trusted-view')).toBeVisible();
    await expect(page.locator('#tv-body')).toContainText('hasn\u2019t checked in');
    await expect(page.locator('#tv-body')).toContainText('House');
    await expect(page.locator('#tv-body')).toContainText('Jane Doe');

    await page.click('#tv-resolve-btn');
    await expect(page.locator('#tv-resolve-status')).toContainText('Notifications stopped');
  });

  test('invalid link shows an error message', async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => { localStorage.clear(); sessionStorage.clear(); });

    await page.route(`${API_BASE}/trusted/access?token=bad`, async (route) => {
      await route.fulfill({ status: 400, contentType: 'application/json', body: '{"detail":"This link is invalid or has already been used."}' });
    });

    await page.goto('/?trusted=bad');
    await expect(page.locator('#tv-body')).toContainText('This link isn\u2019t available');
  });
});
