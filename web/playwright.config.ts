import { defineConfig } from '@playwright/test'

/**
 * Browser checks of the production build with mocked API responses (`make e2e`, which
 * builds first; also run by `make prod-check`). Screenshots go to SCREENSHOT_DIR.
 */
export default defineConfig({
  testDir: './e2e',
  outputDir: './test-results',
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
  use: { browserName: 'chromium', baseURL: 'http://127.0.0.1:8471' },
})
