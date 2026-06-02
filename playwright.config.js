// @ts-check
const { defineConfig } = require('@playwright/test');

/**
 * Emergency Exit — Playwright Configuration
 *
 * HOW IT WORKS:
 * 1. Playwright spins up a local web server serving index.html on port 3000
 * 2. Tests open a real browser (Chromium) pointed at http://localhost:3000
 * 3. Tests interact with the app like a real user would — clicking, typing, etc.
 *
 * This means tests do NOT need the Railway backend to be running.
 * API calls are intercepted ("mocked") so tests are fast and offline.
 */
module.exports = defineConfig({
  // Where to find test files
  testDir: './tests/frontend',

  // Max time a single test can run (30 seconds should be plenty)
  timeout: 30_000,

  // Retry failed tests once — helps catch flaky timing issues
  retries: 1,

  // Run tests one at a time (not in parallel) — simpler for a single-page app
  workers: 1,

  // Reporter: shows results in terminal + generates an HTML report
  reporter: [
    ['list'],
    ['html', { open: 'never', outputFolder: 'playwright-report' }],
  ],

  // Browser settings
  use: {
    // Base URL — all page.goto('/') calls resolve to this
    baseURL: 'http://localhost:3000',

    // Take a screenshot on failure (helps debug CI failures)
    screenshot: 'only-on-failure',

    // Capture execution trace on first retry (detailed timeline of what happened)
    trace: 'on-first-retry',
  },

  // Only test in Chromium (keeps CI fast — add Firefox/WebKit later if needed)
  projects: [
    {
      name: 'chromium',
      use: { browserName: 'chromium' },
    },
  ],

  // Start a local web server before running tests
  // This serves the root directory (where index.html lives) on port 3000
  webServer: {
    command: 'npx serve . -l 3000 --no-clipboard',
    port: 3000,
    reuseExistingServer: !process.env.CI,
  },
});
