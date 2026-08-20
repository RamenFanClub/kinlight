/**
 * Emergency Exit — Playwright Test Helpers
 *
 * Shared utilities used by all test files. The key concept here is "API mocking":
 * instead of hitting the real Railway backend, we intercept network requests and
 * return fake responses. This makes tests fast, offline, and deterministic.
 *
 * IMPORTANT ORDER OF OPERATIONS:
 * Routes MUST be registered before page.goto() is called.
 * In every test file, the correct pattern is:
 *   1. await page.goto('/')          ← page starts loading
 *   2. await clearStorage(page)      ← wipe any leftover state
 *   3. await mockAPI(page, {...})    ← register route intercepts
 *   4. await page.reload()          ← reload so the mocks are in place
 *   5. await loginViaUI(page)        ← now login will work
 */

const API_BASE = 'https://api.kinlight.app';

/**
 * Mock all API calls so tests don't need the real backend.
 * MUST be called before any navigation that triggers API calls.
 *
 * @param {import('@playwright/test').Page} page
 * @param {object} options
 * @param {object} [options.vault] - Vault data to return from GET /vault
 * @param {object} [options.user] - User object to return from login
 * @param {boolean} [options.loginShouldFail] - If true, login returns 401
 */
async function mockAPI(page, options = {}) {
  const {
    vault = null,
    user = { name: 'Test User', email: 'tester_01@example.com' },
    loginShouldFail = false,
  } = options;

  await page.route(`${API_BASE}/auth/login`, async (route) => {
    if (loginShouldFail) {
      await route.fulfill({ status: 401, contentType: 'application/json', body: '{"error":"Invalid credentials"}' });
    } else {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ token: 'fake-jwt-token-for-testing', user }),
      });
    }
  });

  await page.route(`${API_BASE}/vault`, async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true, vault: vault }),
      });
    } else {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
    }
  });

  await page.route(`${API_BASE}/vault/sync`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
  });

  await page.route(`${API_BASE}/checkin`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
  });
}

/**
 * Log in via the UI. Assumes mockAPI() has already been called.
 */
async function loginViaUI(page, email = 'tester_01@example.com', password = 'Benny#07') {
  await page.fill('#li-user', email);
  await page.fill('#li-pass', password);
  await page.click('.login-btn');
  await page.waitForSelector('#login-wall', { state: 'hidden', timeout: 8000 });
}

/**
 * Full setup: goto, clear storage, mock API, reload.
 * Use this in beforeEach instead of rolling your own sequence.
 *
 * @param {import('@playwright/test').Page} page
 * @param {object} mockOptions - Options passed to mockAPI
 */
async function setupPage(page, mockOptions = {}) {
  await page.goto('/');
  await page.evaluate(() => { localStorage.clear(); sessionStorage.clear(); });
  await mockAPI(page, mockOptions);
  await page.reload();
}

/**
 * Clear all browser storage.
 */
async function clearStorage(page) {
  await page.evaluate(() => { localStorage.clear(); sessionStorage.clear(); });
}

/**
 * Get the current vault state from localStorage.
 */
async function getVaultState(page) {
  return page.evaluate(() => {
    const raw = localStorage.getItem('ee_v3');
    return raw ? JSON.parse(raw) : null;
  });
}

/**
 * Build a vault state with sensible defaults.
 */
function buildVault(overrides = {}) {
  return {
    assets: [],
    wishes: [],
    kin: [],
    will: null,
    suppDocs: [],
    lastCheckin: null,
    fc: 2,
    fu: 'months',
    gp: 7,
    v: 'face',
    notifySeq: 'in_order',
    notifyProto: 'ping_then_notify',
    log: [],
    saveCount: 0,
    ...overrides,
  };
}

/**
 * Build a 100%-complete vault (all 7 checks pass).
 */
function buildFullVault(overrides = {}) {
  return buildVault({
    assets: [{ id: 1, name: 'House', category: 'Property', value: 500000, details: '', beneficiary: 'Jane', notes: '' }],
    wishes: [{ id: 2, category: 'Funeral & Service', title: 'Cremation', details: '', priority: 'high' }],
    will: { status: 'signed', date: '2024-01-01', solicitor: 'Smith & Co', loc1: 'Office', loc2: '', notes: '' },
    suppDocs: [{ id: 3, type: 'Statement of Wishes', name: 'SOW 2024', loc: 'Safe', notes: '' }],
    kin: [{ id: 4, first: 'Jane', last: 'Doe', rel: 'Partner', email: 'jane@test.com', phone: '', notifyVia: 'email', order: 1, letter: '' }],
    lastCheckin: Date.now(),
    ...overrides,
  });
}

module.exports = {
  API_BASE,
  mockAPI,
  loginViaUI,
  setupPage,
  clearStorage,
  getVaultState,
  buildVault,
  buildFullVault,
};
