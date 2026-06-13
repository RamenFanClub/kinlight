const { test, expect } = require('@playwright/test');
const { mockAPI, loginViaUI, setupPage } = require('./helpers');

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

  test('empty username/password shows validation error', async ({ page }) => {
    await page.click('.login-btn');
    const err = page.locator('#login-err');
    await expect(err).toBeVisible();
    await expect(err).toHaveText('Please enter your username and password.');
  });

  test('wrong credentials show error message', async ({ page }) => {
    await setupPage(page, { loginShouldFail: true });
    await page.fill('#li-user', 'wrong_user');
    await page.fill('#li-pass', 'wrong_pass');
    await page.click('.login-btn');
    const err = page.locator('#login-err');
    await expect(err).toBeVisible();
    await expect(err).toHaveText('Incorrect username or password.');
  });

  test('successful login hides wall and shows greeting', async ({ page }) => {
    await setupPage(page, { user: { name: 'Sarah Nguyen', username: 'tester_01' } });
    await loginViaUI(page);
    await expect(page.locator('#login-wall')).toHaveClass(/hidden/);
    const greeting = page.locator('#user-greeting');
    await expect(greeting).toBeVisible();
    await expect(greeting).toHaveText('Hi, Sarah');
    await expect(page.locator('#logout-btn')).toBeVisible();
  });

  test('Enter key submits login form', async ({ page }) => {
    await setupPage(page, { user: { name: 'Test User', username: 'tester_01' } });
    await page.fill('#li-user', 'tester_01');
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

});
