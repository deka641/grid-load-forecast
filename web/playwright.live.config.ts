import { defineConfig } from '@playwright/test'

/**
 * Checks against a running production-like stack with real data (`make prod-check`
 * starts one on port 8461). Screenshots go to SCREENSHOT_DIR.
 */
export default defineConfig({
  testDir: './live',
  outputDir: './test-results-live',
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
  use: { browserName: 'chromium', baseURL: process.env.LIVE_BASE_URL ?? 'http://127.0.0.1:8461' },
})
