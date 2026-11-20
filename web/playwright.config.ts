import { defineConfig } from '@playwright/test'

/**
 * Browser checks against the production build (`npm run build` first). Not part of
 * `make verify`; meant for a later prod check. Screenshots go to SCREENSHOT_DIR.
 */
export default defineConfig({
  testDir: './e2e',
  outputDir: './test-results',
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
  use: { browserName: 'chromium', baseURL: 'http://127.0.0.1:8471' },
})
