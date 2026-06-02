/**
 * Emergency Exit — Playwright Test Helpers
 *
 * Shared utilities used by all test files. The key concept here is "API mocking":
 * instead of hitting the real Railway backend, we intercept network requests and
 * return fake responses. This makes tests fast, offline, and deterministic.
 *
 * GLOSSARY:
 * - "route" = Playwright's way of intercepting network requests before they leave the browser
 * - "fulfill" = respond to an intercepted request with fake data
 * - "localStorage" = browser storage that persists between page loads (used for vault cache)
 * - "sessionStorage" = browser storage that clears when the tab closes (used for auth tokens)
 */

const API_BASE = 'https://emergency-exit-production.up.railway.app';

/**
 * Mock all API calls so tests don't need the real backend.
 *
 * WHAT THIS DOES:
 * - Intercepts POST /auth/login → returns a fake JWT token + user object
 * - Intercepts GET /vault → returns whatever vault data you pass in
 * - Intercepts POST /vault/sync → returns success (no-op)
 * - Intercepts POST /checkin → returns success
 *
 * @param {import('@playwright/test').Page} page - The browser page
 * @param {object} options - Configuration
 * @param {object} [options.vault] - Vault data to return from GET /vault
 * @param {object} [options.user] - User object to return from login
 * @param {boolean} [options.loginShouldFail] - If true, login returns 401
 */
async function mockAPI(page, options = {}) {
  const {
    vault = null,
    user = { name: 'Test User', username: 'tester_01' },
    loginShouldFail = false,
  } = options;

  // Mock login endpoint
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

  // Mock vault fetch
  await page.route(`${API_BASE}/vault`, async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true, vault: vault }),
      });
    } else {
      // POST/PUT — just acknowledge
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
    }
  });

  // Mock vault sync (fire-and-forget from the app's perspective)
  await page.route(`${API_BASE}/vault/sync`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
  });

  // Mock check-in
  await page.route(`${API_BASE}/checkin`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
  });
}

/**
 * Log in via the UI. Types username + password and clicks Sign In.
 *
 * @param {import('@playwright/test').Page} page
 * @param {string} [username='tester_01']
 * @param {string} [password='Benny#07']
 */
async function loginViaUI(page, username = 'tester_01', password = 'Benny#07') {
  await page.fill('#li-user', username);
  await page.fill('#li-pass', password);
  await page.click('.login-btn');
  // Wait for the login wall to disappear
  await page.waitForSelector('#login-wall', { state: 'hidden', timeout: 5000 });
}

/**
 * Inject vault state directly into localStorage, bypassing the login flow.
 * Useful for testing specific UI states without going through login.
 *
 * @param {import('@playwright/test').Page} page
 * @param {object} state - The vault state to inject into ee_v3
 */
async function injectVaultState(page, state) {
  await page.evaluate((s) => {
    localStorage.setItem('ee_v3', JSON.stringify(s));
  }, state);
}

/**
 * Clear all browser storage — fresh slate for each test.
 */
async function clearStorage(page) {
  await page.evaluate(() => {
    localStorage.clear();
    sessionStorage.clear();
  });
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
 * Build a vault state object with sensible defaults.
 * Override any field by passing it in the overrides object.
 *
 * EXAMPLE:
 *   buildVault({ assets: [{ id: 1, name: 'House', category: 'Property' }] })
 *   → returns a full vault state with one asset and everything else empty
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
 * Build a vault that is 100% complete (all 7 checks pass).
 * Useful for testing "Everything is in order" hero state.
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
  injectVaultState,
  clearStorage,
  getVaultState,
  buildVault,
  buildFullVault,
};
