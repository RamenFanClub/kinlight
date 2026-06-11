/**
 * F58 — Contacts CRUD + Personal Letter Tests
 */
const { test, expect } = require('@playwright/test');
const { loginViaUI, setupPage, buildVault } = require('./helpers');

test.describe('Contacts CRUD', () => {

  test.beforeEach(async ({ page }) => {
    await setupPage(page, { vault: buildVault() });
    await loginViaUI(page);
    await page.click('#n-kin');
  });

  test('empty state shows on Contacts screen', async ({ page }) => {
    const text = await page.locator('#k-list').textContent();
    expect(text).toContain('No contacts added yet');
  });

  test('can open Add Contact modal', async ({ page }) => {
    await page.locator('#s-kin .ob').click();
    await expect(page.locator('#km')).toHaveClass(/on/);
    await expect(page.locator('#km-title')).toHaveText('Add Contact');
  });

  test('saving contact without first name shows validation', async ({ page }) => {
    await page.locator('#s-kin .ob').click();
    await page.locator('#km').getByText('Add Contact').last().click();
    await expect(page.locator('.toast')).toContainText('first name');
  });

  test('can add a contact and they appear in the list', async ({ page }) => {
    await page.locator('#s-kin .ob').click();
    await page.fill('#kf', 'Sarah');
    await page.fill('#kl', 'Nguyen');
    await page.fill('#kr', 'Partner');
    await page.fill('#ke', 'sarah@example.com');
    await page.locator('#km').getByText('Add Contact').last().click();
    await expect(page.locator('#k-list')).toContainText('Sarah');
    await expect(page.locator('#k-list')).toContainText('Nguyen');
  });

  test('adding a contact updates completeness score', async ({ page }) => {
    const initialPct = await page.locator('#cp-lbl').textContent();
    expect(parseInt(initialPct)).toBe(0);
    await page.locator('#s-kin .ob').click();
    await page.fill('#kf', 'Jane');
    await page.fill('#ke', 'jane@example.com');
    await page.locator('#km').getByText('Add Contact').last().click();
    await page.click('#n-home');
    const newPct = await page.locator('#cp-lbl').textContent();
    expect(parseInt(newPct)).toBeGreaterThan(0);
  });

  test('contact shows notify method', async ({ page }) => {
    await setupPage(page, {
      vault: buildVault({
        kin: [{ id: 1, first: 'Jane', last: 'Doe', rel: 'Partner', email: 'j@t.com', phone: '', notifyVia: 'email', order: 1, letter: '' }]
      })
    });
    await loginViaUI(page);
    await page.click('#n-kin');
    await expect(page.locator('#k-list')).toContainText('Email');
  });

  test('can delete a contact', async ({ page }) => {
    await setupPage(page, {
      vault: buildVault({
        kin: [{ id: 1, first: 'Delete Me', last: '', rel: '', email: '', phone: '', notifyVia: 'email', order: 1, letter: '' }]
      })
    });
    await loginViaUI(page);
    await page.click('#n-kin');
    await expect(page.locator('#k-list')).toContainText('Delete Me');
    await page.locator('#k-list .del-btn').first().click();
    await expect(page.locator('#k-list')).not.toContainText('Delete Me');
  });

  test('contact with letter shows "Letter written" pill', async ({ page }) => {
    await setupPage(page, {
      vault: buildVault({
        kin: [{ id: 1, first: 'Jane', last: 'Doe', rel: 'Partner', email: 'j@t.com', phone: '', notifyVia: 'email', order: 1, letter: 'Dear Jane, I love you.' }]
      })
    });
    await loginViaUI(page);
    await page.click('#n-kin');
    await expect(page.locator('#k-list')).toContainText('Letter written');
  });

  test('contact without letter shows "No letter yet" pill', async ({ page }) => {
    await setupPage(page, {
      vault: buildVault({
        kin: [{ id: 1, first: 'Jane', last: 'Doe', rel: 'Partner', email: 'j@t.com', phone: '', notifyVia: 'email', order: 1, letter: '' }]
      })
    });
    await loginViaUI(page);
    await page.click('#n-kin');
    await expect(page.locator('#k-list')).toContainText('No letter yet');
  });

});
