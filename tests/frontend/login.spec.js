/**
 * F58 — Login Flow Tests
 *
 * Tests the login wall, authentication, error handling, and logout.
 * These are the most critical tests because if login breaks, NOTHING works.
 */
const { test, expect } = require('@playwright/test');
const { mockAPI, loginViaUI, clearStorage } = require('./helpers');

test.describe('Login Flow', () => {

  test.beforeEach(async ({ page }) => {
    // Clear storage so each test starts fresh (no leftover sessions)
    await page.goto('/');
    await clearStorage(page);
    await page.reload();
  });

  test('login wall is visible on fresh load', async ({ page }) => {
    // The #login-wall should be visible and NOT have the 'hidden' class
    const wall = page.locator('#login-wall');
    await expect(wall).toBeVisible();
    await expect(wall).not.toHaveClass(/hidden/);
  });

  test('login subtitle says "Sign in to your account." (F57)', async ({ page }) => {
    // F57 removed tester language — verify the updated copy
    const subtitle = page.locator('.login-sub');
    await expect(subtitle).toHaveText('Sign in to your account.');
  });

  test('empty username/password shows validation error', async ({ page }) => {
    await page.click('.login-btn');
    const err = page.locator('#login-err');
    await expect(err).toBeVisible();
    await expect(err).toHaveText('Please enter your username and password.');
  });

  test('wrong credentials show error message', async ({ page }) => {
    await mockAPI(page, { loginShouldFail: true });
    await page.fill('#li-user', 'wrong_user');
    await page.fill('#li-pass', 'wrong_pass');
    await page.click('.login-btn');
    const err = page.locator('#login-err');
    await expect(err).toBeVisible();
    await expect(err).toHaveText('Incorrect username or password.');
  });

  test('successful login hides wall and shows greeting', async ({ page }) => {
    await mockAPI(page, { user: { name: 'Sarah Nguyen', username: 'tester_01' } });
    await loginViaUI(page);

    // Login wall should now be hidden
    await expect(page.locator('#login-wall')).toHaveClass(/hidden/);

    // Greeting should show first name only
    const greeting = page.locator('#user-greeting');
    await expect(greeting).toBeVisible();
    await expect(greeting).toHaveText('Hi, Sarah');

    // Logout button should be visible
    await expect(page.locator('#logout-btn')).toBeVisible();
  });

  test('Enter key submits login form', async ({ page }) => {
    await mockAPI(page, { user: { name: 'Test User', username: 'tester_01' } });
    await page.fill('#li-user', 'tester_01');
    await page.fill('#li-pass', 'Benny#07');
    await page.press('#li-pass', 'Enter');
    await expect(page.locator('#login-wall')).toHaveClass(/hidden/);
  });

  test('logout clears session and shows login wall', async ({ page }) => {
    await mockAPI(page);
    await loginViaUI(page);

    // Verify we're logged in
    await expect(page.locator('#login-wall')).toHaveClass(/hidden/);

    // Click logout
    await page.click('#logout-btn');

    // Login wall should reappear
    await expect(page.locator('#login-wall')).toBeVisible();
    await expect(page.locator('#login-wall')).not.toHaveClass(/hidden/);

    // Session storage should be cleared
    const token = await page.evaluate(() => sessionStorage.getItem('ee_token'));
    expect(token).toBeNull();
  });

});
