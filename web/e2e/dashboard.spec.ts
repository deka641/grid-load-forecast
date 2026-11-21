import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'
import type { Page, Route } from '@playwright/test'
import type { Server } from 'node:http'
import { mkdirSync } from 'node:fs'
import { join, resolve } from 'node:path'

import * as fixtures from './fixtures.ts'
import { serveDist } from './server.ts'

const PORT = 8471
const DIST = resolve(import.meta.dirname, '..', 'dist')
const SHOTS = process.env.SCREENSHOT_DIR ?? resolve(import.meta.dirname, '..', 'test-results')

let server: Server

test.beforeAll(async () => {
  mkdirSync(SHOTS, { recursive: true })
  server = await serveDist(DIST, PORT)
})

test.afterAll(async () => {
  await new Promise((done) => server.close(done))
})

interface MockOptions {
  status?:
    ReturnType<typeof fixtures.status> | ReturnType<typeof fixtures.statusBeforeFirstSnapshot>
  withPrice?: boolean
  forecastError?: { error: number; detail: string }
  onForecast?: () => void
  degradedChecks?: string[]
  days?: string[]
}

async function expectNoSeriousAxe(page: Page) {
  const axe = await new AxeBuilder({ page }).analyze()
  const serious = axe.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
  expect(
    serious.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`),
  ).toEqual([])
}

async function mockApi(page: Page, options: MockOptions = {}) {
  await page.route('**/api/v1/**', async (route: Route) => {
    const url = new URL(route.request().url())
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    switch (url.pathname) {
      case '/api/v1/status':
        return json(options.status ?? fixtures.status())
      case '/api/v1/series':
        return json(fixtures.seriesCatalog(options.withPrice ?? true))
      case '/api/v1/forecasts/days':
        return json({ days: options.days ?? fixtures.forecastDays(), limit: 400 })
      case '/api/v1/forecasts':
        options.onForecast?.()
        if (options.forecastError) {
          return json({ detail: options.forecastError.detail }, options.forecastError.error)
        }
        return json(
          fixtures.forecastDay(
            url.searchParams.get('delivery_day') ?? fixtures.LATEST_DAY,
            options.degradedChecks,
          ),
        )
      case '/api/v1/backtest/metrics':
        return json(fixtures.backtest())
      case '/api/v1/actuals':
        return json(
          fixtures.actuals(
            (url.searchParams.get('series') ?? '').split(','),
            url.searchParams.get('start') ?? fixtures.TODAY,
            url.searchParams.get('end') ?? fixtures.TODAY,
          ),
        )
      default:
        return json({ detail: 'not found' }, 404)
    }
  })
}

/** Collect console errors, page errors and CSP violations. */
function watchProblems(page: Page): string[] {
  const problems: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error' || message.type() === 'warning') {
      problems.push(`console ${message.type()}: ${message.text()}`)
    }
  })
  page.on('pageerror', (error) => problems.push(`page error: ${error.message}`))
  return problems
}

async function cspViolations(page: Page): Promise<string[]> {
  return page.evaluate(() => (window as unknown as { __csp?: string[] }).__csp ?? [])
}

async function open(page: Page) {
  await page.addInitScript(() => {
    const store: string[] = []
    ;(window as unknown as { __csp: string[] }).__csp = store
    document.addEventListener('securitypolicyviolation', (event) => {
      store.push(`${event.violatedDirective}: ${event.blockedURI} ${event.sample}`)
    })
  })
  await page.goto('/')
  await expect(page.locator('.headline')).toBeVisible()
  await expect(page.locator('.chart-canvas canvas')).toHaveCount(4)
  await page.evaluate(() => document.fonts.ready)
}

/** Hover each chart so that the tooltips render (they set innerHTML). */
async function hoverCharts(page: Page) {
  for (const chart of await page.locator('.chart-canvas').all()) {
    await chart.scrollIntoViewIfNeeded()
    const box = await chart.boundingBox()
    if (box) {
      await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.5)
      await expect(chart.locator('.chart-tooltip')).toBeVisible()
    }
  }
  await page.mouse.move(0, 0)
}

for (const scheme of ['light', 'dark'] as const) {
  for (const width of [1440, 1024, 768, 390]) {
    test(`renders cleanly in ${scheme} at ${String(width)} px`, async ({ browser }) => {
      const context = await browser.newContext({
        colorScheme: scheme,
        viewport: { width, height: 900 },
        deviceScaleFactor: width < 600 ? 2 : 1,
        timezoneId: 'Europe/Berlin',
      })
      const page = await context.newPage()
      const problems = watchProblems(page)
      await mockApi(page)
      await open(page)

      await expect(page.locator('.headline')).toHaveText(
        /^Forecast peak for Saturday 21 November: \d+\.\d GW at \d\d:00$/,
      )
      await page.screenshot({
        path: join(SHOTS, `dashboard-${scheme}-${String(width)}.png`),
        fullPage: true,
      })
      await hoverCharts(page)

      if (width < 600) {
        const mix = page.locator('.chart-mix .chart-canvas')
        await mix.scrollIntoViewIfNeeded()
        const box = await mix.boundingBox()
        if (box) {
          await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5)
          await page.screenshot({ path: join(SHOTS, `tooltip-mix-${scheme}-390.png`) })
          await page.mouse.move(0, 0)
        }
        // Touch targets: every control is at least 44 px high.
        const controls = page.locator('button, select, .theme-option span, .site-footer a')
        for (const control of await controls.all()) {
          const size = await control.boundingBox()
          expect(size?.height ?? 0).toBeGreaterThanOrEqual(44)
        }
      }

      const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth)
      expect(scrollWidth).toBeLessThanOrEqual(width)
      // The backtest table fits without sideways scrolling.
      const table = page.locator('.table-scroll')
      const fits = await table.evaluate((el) => el.scrollWidth <= el.clientWidth)
      expect(fits).toBe(true)

      await expectNoSeriousAxe(page)

      expect(await cspViolations(page)).toEqual([])
      expect(problems).toEqual([])
      await context.close()
    })
  }
}

for (const scheme of ['light', 'dark'] as const) {
  test(`keeps the opened data tables accessible in ${scheme}`, async ({ browser }) => {
    const context = await browser.newContext({
      colorScheme: scheme,
      viewport: { width: 1440, height: 900 },
      timezoneId: 'Europe/Berlin',
    })
    const page = await context.newPage()
    const problems = watchProblems(page)
    await mockApi(page)
    await open(page)

    // The newest day has no actual load yet: the legend says so and the tooltip leaves it out.
    const lines = page.getByRole('list', { name: 'Lines in the forecast chart' })
    await expect(lines.locator('.is-empty')).toHaveText('Actual (not yet published)')
    const forecastCanvas = page.locator('.chart-forecast .chart-canvas')
    const tooltip = forecastCanvas.locator('.chart-tooltip')
    const hoverAt = async (share: number) => {
      const box = await forecastCanvas.boundingBox()
      expect(box).not.toBeNull()
      if (box) {
        await page.mouse.move(box.x + box.width * share, box.y + box.height * 0.5)
      }
      await expect(tooltip).toBeVisible()
    }
    await hoverAt(0.5)
    await expect(tooltip).not.toContainText('Actual')
    await page.mouse.move(0, 0)

    // 20 November is published up to the early afternoon: the row is there only for those hours.
    await page.getByLabel('Delivery day').selectOption('2026-11-20')
    await expect(page.locator('.headline')).toContainText('Friday 20 November')
    await expect(lines.locator('.is-empty')).toHaveCount(0)
    await hoverAt(0.3)
    await expect(tooltip).toContainText('Actual')
    await hoverAt(0.7)
    await expect(tooltip).not.toContainText('Actual')
    await page.mouse.move(0, 0)

    // Each click renames its button to "Hide data", so open the next "Show data" each time.
    const toggles = page.getByRole('button', { name: 'Show data' })
    await expect(toggles).toHaveCount(4)
    for (let remaining = 4; remaining > 0; remaining -= 1) {
      await toggles.first().click()
      await expect(toggles).toHaveCount(remaining - 1)
    }
    const regions = page.locator('.chart-data-scroll[role="region"]')
    await expect(regions).toHaveCount(4)
    const names = await regions.evaluateAll((list) =>
      list.map((el) => el.getAttribute('aria-label')),
    )
    expect(new Set(names).size).toBe(4)

    // The hourly forecast table scrolls with the keyboard once its box has focus.
    const first = regions.first()
    await first.focus()
    await expect(first).toBeFocused()
    await page.keyboard.press('PageDown')
    await expect.poll(() => first.evaluate((el) => el.scrollTop)).toBeGreaterThan(0)
    await page.screenshot({
      path: join(SHOTS, `data-tables-${scheme}-1440.png`),
      fullPage: true,
    })

    const axe = await new AxeBuilder({ page }).analyze()
    expect(axe.violations.map((v) => `${v.id} (${v.impact ?? 'unknown'})`)).toEqual([])
    expect(await cspViolations(page)).toEqual([])
    expect(problems).toEqual([])
    await context.close()
  })
}

test('uses Berlin time regardless of the browser time zone', async ({ browser }) => {
  const context = await browser.newContext({ timezoneId: 'America/New_York' })
  const page = await context.newPage()
  await mockApi(page)
  await open(page)
  const peak = fixtures
    .forecastDay(fixtures.LATEST_DAY)
    .points.reduce((a, b) => (b.lightgbm > a.lightgbm ? b : a))
  await expect(page.locator('.headline')).toHaveText(
    `Forecast peak for Saturday 21 November: ${(peak.lightgbm / 1000).toFixed(1)} GW at ${peak.ts.slice(11, 16)}`,
  )
  await expect(page.locator('.freshness')).toContainText('Data up to 20 Nov 2026, 13:30')
  await context.close()
})

test('switches days, handles the 25 hour day and toggles the theme', async ({ browser }) => {
  const context = await browser.newContext({ colorScheme: 'light' })
  const page = await context.newPage()
  const problems = watchProblems(page)
  await mockApi(page)
  await open(page)

  await page.getByLabel('Delivery day').selectOption('2026-10-25')
  await expect(page.locator('.headline')).toContainText('Sunday 25 October')
  await expect(page.locator('.clock-change')).toContainText('25 hours')
  await expect(page.getByText('This day', { exact: true })).toBeVisible()
  await page.locator('.chart-forecast').scrollIntoViewIfNeeded()
  await page.locator('.chart-forecast').screenshot({ path: join(SHOTS, 'chart-25h-day.png') })
  await page.getByRole('button', { name: 'Previous day' }).click()
  await expect(page.locator('.headline')).toContainText('Saturday 24 October')

  await page.getByLabel('Dark').check()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.evaluate(() => {
    window.scrollTo(0, 0)
  })
  await page.screenshot({ path: join(SHOTS, 'dashboard-dark-after-toggle.png') })
  await expectNoSeriousAxe(page)
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await expect(page.locator('.chart-canvas canvas')).toHaveCount(4)
  await hoverCharts(page)
  expect(await cspViolations(page)).toEqual([])
  expect(problems).toEqual([])
  await context.close()
})

test('hides the price chart when prices are switched off', async ({ browser }) => {
  const page = await browser.newPage()
  await mockApi(page, { withPrice: false })
  await page.goto('/')
  await expect(page.locator('.chart-canvas canvas')).toHaveCount(3)
  await expect(page.getByText('HEDWIG')).toHaveCount(0)
  await page.close()
})

test('marks stale data', async ({ browser }) => {
  const page = await browser.newPage()
  await mockApi(page, { status: fixtures.status(8 * 3600) })
  await page.goto('/')
  await expect(page.locator('.freshness-stale')).toContainText('more than 6 hours old')
  await page.close()
})

test('shows the first-start notice without accessibility problems', async ({ browser }) => {
  const context = await browser.newContext()
  const page = await context.newPage()
  const problems = watchProblems(page)
  await mockApi(page, { status: fixtures.statusBeforeFirstSnapshot() })
  await page.goto('/')
  await expect(page.getByText(/^No data yet\. The pipeline loads the full history/)).toBeVisible()
  await expect(page.getByText('Last pipeline run: none yet')).toBeVisible()
  await expectNoSeriousAxe(page)
  expect(problems).toEqual([])
  await context.close()
})

test('offers a retry when the server is busy', async ({ browser }) => {
  const context = await browser.newContext({ colorScheme: 'dark' })
  const page = await context.newPage()
  let attempts = 0
  await mockApi(page, {
    forecastError: { error: 503, detail: 'the server is busy; try again shortly' },
    onForecast: () => {
      attempts += 1
    },
  })
  await page.goto('/')
  const alert = page.getByRole('alert')
  // The client retries a busy 503 twice before it shows the error.
  await expect(alert).toContainText('the server is busy; try again shortly', { timeout: 15_000 })
  expect(attempts).toBe(3)
  await expectNoSeriousAxe(page)
  await alert.getByRole('button', { name: 'Retry' }).click()
  await expect.poll(() => attempts, { timeout: 15_000 }).toBeGreaterThan(3)
  await context.close()
})

for (const scheme of ['light', 'dark'] as const) {
  test(`explains a degraded forecast in ${scheme}`, async ({ browser }) => {
    const context = await browser.newContext({
      colorScheme: scheme,
      viewport: { width: 1024, height: 900 },
      timezoneId: 'Europe/Berlin',
    })
    const page = await context.newPage()
    const problems = watchProblems(page)
    await mockApi(page, { degradedChecks: ['cutoff_hour_missing', 'lag_1d', 'lag_2d', 'x_new'] })
    await open(page)
    const notice = page.locator('.forecast-body .notice-warn')
    await expect(notice).toContainText('Computed with incomplete recent load data.')
    await expect(notice.locator('li')).toHaveText([
      'the load of the last hour before the cutoff (09:00 to 10:00 on the day before)',
      'for some hours, the load of the same hour one and two days earlier',
      'other input data',
    ])
    await notice.screenshot({ path: join(SHOTS, `degraded-${scheme}-1024.png`) })
    await expectNoSeriousAxe(page)
    expect(await cspViolations(page)).toEqual([])
    expect(problems).toEqual([])
    await context.close()
  })
}

test('shows no degraded notice for a complete forecast', async ({ browser }) => {
  const page = await browser.newPage()
  await mockApi(page)
  await open(page)
  await expect(page.locator('.notice-warn')).toHaveCount(0)
  await page.close()
})

for (const scheme of ['light', 'dark'] as const) {
  test(`shows a single forecast day without dead controls in ${scheme}`, async ({ browser }) => {
    const context = await browser.newContext({
      colorScheme: scheme,
      viewport: { width: 390, height: 900 },
      deviceScaleFactor: 2,
      timezoneId: 'Europe/Berlin',
    })
    const page = await context.newPage()
    const problems = watchProblems(page)
    await mockApi(page, { days: [fixtures.LATEST_DAY] })
    await open(page)
    await expect(page.locator('.day-single')).toHaveText('Delivery day Sat 21 Nov 2026')
    await expect(page.getByRole('button', { name: /day$/ })).toHaveCount(0)
    await page.screenshot({ path: join(SHOTS, `single-day-${scheme}-390.png`) })
    await expectNoSeriousAxe(page)
    expect(await cspViolations(page)).toEqual([])
    expect(problems).toEqual([])
    await context.close()
  })
}
