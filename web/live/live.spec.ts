import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { join, resolve } from 'node:path'

const SHOTS = process.env.SCREENSHOT_DIR ?? resolve(import.meta.dirname, '..', 'test-results-live')

test.beforeAll(() => {
  mkdirSync(SHOTS, { recursive: true })
})

function watch(page: Page): string[] {
  const problems: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error' || message.type() === 'warning') {
      problems.push(`console ${message.type()}: ${message.text()}`)
    }
  })
  page.on('pageerror', (error) => problems.push(`page error: ${error.message}`))
  return problems
}

for (const scheme of ['light', 'dark'] as const) {
  for (const width of [1440, 390]) {
    test(`real data renders cleanly in ${scheme} at ${String(width)} px`, async ({ browser }) => {
      const context = await browser.newContext({
        colorScheme: scheme,
        viewport: { width, height: 900 },
        deviceScaleFactor: width < 600 ? 2 : 1,
        timezoneId: 'Europe/Berlin',
      })
      const page = await context.newPage()
      const problems = watch(page)
      await page.addInitScript(() => {
        const store: string[] = []
        ;(window as unknown as { __csp: string[] }).__csp = store
        document.addEventListener('securitypolicyviolation', (event) => {
          store.push(`${event.violatedDirective}: ${event.blockedURI}`)
        })
      })
      await page.goto('/')
      await expect(page.locator('.headline')).toHaveText(/^Forecast peak for /)
      await expect(page.locator('.chart-canvas canvas')).toHaveCount(4)
      await page.evaluate(() => document.fonts.ready)
      await page.screenshot({
        path: join(SHOTS, `live-${scheme}-${String(width)}.png`),
        fullPage: true,
      })

      const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth)
      expect(scrollWidth).toBeLessThanOrEqual(width)
      const axe = await new AxeBuilder({ page }).analyze()
      const serious = axe.violations.filter(
        (v) => v.impact === 'serious' || v.impact === 'critical',
      )
      expect(serious.map((v) => v.id)).toEqual([])
      const csp = await page.evaluate(() => (window as unknown as { __csp?: string[] }).__csp ?? [])
      expect(csp).toEqual([])
      expect(problems).toEqual([])
      await context.close()
    })
  }
}
