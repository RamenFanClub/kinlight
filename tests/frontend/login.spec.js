const { test, expect } = require('@playwright/test');
const { mockAPI, loginViaUI, setupPage, buildVault } = require('./helpers');

test.describe('Login Flow', () => {

  test.beforeEach(async ({ page }) => {
    await setupPage(page);
  });

  test('login wall is visible on fresh load', async ({ page }) => {
    const wall = page.locator('#login-wall');
    await expect(wall).toBeVisible();
    await expect(wall).not.toHaveClass(/hidden/);
  });

  test('login subtitle says "Sign in to your account." (F57)', async ({ page }) => {
    const subtitle = page.locator('#lv-login .login-sub');
    await expect(subtitle).toHaveText('Sign in to your account.');
  });

  test('empty email/password shows validation error', async ({ page }) => {
    await page.click('.login-btn');
    const err = page.locator('#login-err');
    await expect(err).toBeVisible();
    await expect(err).toHaveText('Please enter your email and password.');
  });

  test('wrong credentials show error message', async ({ page }) => {
    await setupPage(page, { loginShouldFail: true });
    await page.fill('#li-user', 'wrong@example.com');
    await page.fill('#li-pass', 'wrong_pass');
    await page.click('.login-btn');
    const err = page.locator('#login-err');
    await expect(err).toBeVisible();
    await expect(err).toHaveText('Incorrect email or password.');
  });

  test('successful login hides wall and shows greeting', async ({ page }) => {
    await setupPage(page, { user: { name: 'Sarah Nguyen', email: 'sarah@example.com' } });
    await loginViaUI(page);
    await expect(page.locator('#login-wall')).toHaveClass(/hidden/);
    const greeting = page.locator('#user-greeting');
    await expect(greeting).toBeVisible();
    await expect(greeting).toHaveText('Hi, Sarah');
    await expect(page.locator('#logout-btn')).toBeVisible();
  });

  test('Enter key submits login form', async ({ page }) => {
    await setupPage(page, { user: { name: 'Test User', email: 'tester_01@example.com' } });
    await page.fill('#li-user', 'tester_01@example.com');
    await page.fill('#li-pass', 'Benny#07');
    await page.press('#li-pass', 'Enter');
    await page.waitForSelector('#login-wall', { state: 'hidden', timeout: 8000 });
    await expect(page.locator('#login-wall')).toHaveClass(/hidden/);
  });

  test('logout clears session and shows login wall', async ({ page }) => {
    await setupPage(page);
    await loginViaUI(page);
    await expect(page.locator('#login-wall')).toHaveClass(/hidden/);
    await page.click('#logout-btn');
    await expect(page.locator('#login-wall')).toBeVisible();
    await expect(page.locator('#login-wall')).not.toHaveClass(/hidden/);
    const token = await page.evaluate(() => sessionStorage.getItem('ee_token'));
    expect(token).toBeNull();
  });

  test('logout clears the cached vault from localStorage (F99)', async ({ page }) => {
    // F99: the vault content cache (ee_v3) previously survived logout in
    // localStorage indefinitely, leaving plaintext vault data on a shared
    // or stolen device even after the user signed out.
    //
    // The app only writes ee_v3 once it actually loads a non-null vault
    // from the server (see loadFromServer() in index.html) — a fresh
    // login with no vault data (the beforeEach default) never populates
    // it. So this test overrides the default mock with a real vault.
    await setupPage(page, { vault: buildVault() });
    await loginViaUI(page);

    // Confirm the vault cache actually exists before logging out, so this
    // test would fail loudly if the app stops caching at all.
    const cachedBeforeLogout = await page.evaluate(() => localStorage.getItem('ee_v3'));
    expect(cachedBeforeLogout).not.toBeNull();

    await page.click('#logout-btn');

    const cachedAfterLogout = await page.evaluate(() => localStorage.getItem('ee_v3'));
    expect(cachedAfterLogout).toBeNull();
  });

  test('passkey sign-in button is visible (F118)', async ({ page }) => {
    const btn = page.locator('#lv-login .login-btn').filter({ hasText: 'Sign in with passkey' });
    await expect(btn).toBeVisible();
  });

  test('login sends a new-device identifier (F117)', async ({ page }) => {
    await setupPage(page, { user: { name: 'Test User', email: 'tester_01@example.com' } });
    let loginBody = null;
    page.on('request', (req) => {
      if (req.url().includes('/auth/login') && req.method() === 'POST') {
        loginBody = JSON.parse(req.postData() || '{}');
      }
    });
    await loginViaUI(page);
    expect(loginBody).not.toBeNull();
    expect(loginBody.deviceId).toBeTruthy();
    expect(typeof loginBody.deviceName).toBe('string');
    const stored = await page.evaluate(() => localStorage.getItem('ee_device_id'));
    expect(stored).toBe(loginBody.deviceId);
  });

});
