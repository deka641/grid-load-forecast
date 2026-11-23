import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'
import type { Page, Route } from '@playwright/test'
import type { Server } from 'node:http'
import { mkdirSync } from 'node:fs'
import { join, resolve } from 'node:path'

import * as fixtures from './fixtures.ts'
import { CSP, serveDist } from './server.ts'

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
  priceEnd?: number
  priceGap?: readonly [number, number]
  /** Paths that answer 500. */
  failing?: string[]
  /** Data requests (all but status and series) wait for this. */
  hold?: Promise<unknown>
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
    if (options.failing?.includes(url.pathname)) {
      return json({ detail: 'the snapshot could not be read' }, 500)
    }
    if (options.hold && !['/api/v1/status', '/api/v1/series'].includes(url.pathname)) {
      await options.hold
    }
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
            options.priceEnd,
            options.priceGap,
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
  for (const width of [1440, 1024, 768, 750, 700, 641, 390]) {
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
    await expect(notice).toContainText('Computed with incomplete load data.')
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

for (const [scheme, width] of [
  ['light', 1440],
  ['dark', 1440],
  ['light', 390],
  ['dark', 390],
] as const) {
  test(`marks missing prices and negative prices in ${scheme} at ${String(width)} px`, async ({
    browser,
  }) => {
    const context = await browser.newContext({
      colorScheme: scheme,
      viewport: { width, height: 900 },
      deviceScaleFactor: width < 600 ? 2 : 1,
      timezoneId: 'Europe/Berlin',
    })
    const page = await context.newPage()
    const problems = watchProblems(page)
    // Prices end with 19 November, while generation runs into 20 November. The prices of
    // 20 November were due on 19 November, so the missing hours are a gap in the source.
    await mockApi(page, { priceEnd: Date.parse('2026-11-19T22:00:00Z') })
    await open(page)
    await expect(page.locator('.chart-stats')).toContainText(
      'SMARD has published no prices from Fri 20 Nov, 00:00 to 14:00 (UTC+1); the chart shades these hours lighter.',
    )
    await expect(page.locator('.chart-stats')).not.toContainText('last published price')
    await expect(page.locator('.chart-stats')).toContainText(
      'Hourly prices (each the mean of four quarter hours) from',
    )
    await expect(page.locator('.chart-stats')).not.toContainText('negative in 0 of')
    const chart = page.locator('.chart-price')
    await chart.scrollIntoViewIfNeeded()
    await chart.screenshot({ path: join(SHOTS, `price-end-${scheme}-${String(width)}.png`) })
    await expectNoSeriousAxe(page)
    expect(await cspViolations(page)).toEqual([])
    expect(problems).toEqual([])
    await context.close()
  })
}

for (const [scheme, width] of [
  ['light', 1440],
  ['dark', 390],
] as const) {
  test(`marks a price gap inside the range in ${scheme} at ${String(width)} px`, async ({
    browser,
  }) => {
    const context = await browser.newContext({
      colorScheme: scheme,
      viewport: { width, height: 900 },
      deviceScaleFactor: width < 600 ? 2 : 1,
      timezoneId: 'Europe/Berlin',
    })
    const page = await context.newPage()
    const problems = watchProblems(page)
    // No prices for 16 and 17 November; those of today run to its end, past generation.
    await mockApi(page, {
      priceGap: [Date.parse('2026-11-15T23:00:00Z'), Date.parse('2026-11-17T22:00:00Z')],
    })
    await open(page)
    const stats = page.locator('.chart-stats')
    await expect(stats).toContainText(
      'SMARD has published no prices from Mon 16 Nov, 00:00 to Wed 18 Nov, 00:00 (UTC+1); the chart shades these hours lighter.',
    )
    await expect(stats).toContainText('over 120 of 168 hours')
    await expect(stats).not.toContainText('no prices yet')
    const chart = page.locator('.chart-price')
    await chart.scrollIntoViewIfNeeded()
    await chart.screenshot({ path: join(SHOTS, `price-gap-${scheme}-${String(width)}.png`) })
    await expectNoSeriousAxe(page)
    expect(await cspViolations(page)).toEqual([])
    expect(problems).toEqual([])
    await context.close()
  })
}

for (const width of [1440, 390]) {
  test(`names delivery days without a forecast at ${String(width)} px`, async ({ browser }) => {
    const context = await browser.newContext({
      viewport: { width, height: 900 },
      timezoneId: 'Europe/Berlin',
    })
    const page = await context.newPage()
    await mockApi(page, { days: ['2026-11-21', '2026-11-19', '2026-11-18'] })
    await open(page)
    const note = page.getByText('No forecast was issued for Fri 20 Nov 2026.')
    await expect(note).toBeVisible()
    await expect(page.getByRole('button', { name: 'Previous day' })).toHaveAccessibleDescription(
      'No forecast was issued for Fri 20 Nov 2026.',
    )
    // Beside the controls where the row has room, below them otherwise; never over them.
    const selector = await page.locator('.day-selector').boundingBox()
    const box = await note.boundingBox()
    const beside = (box?.x ?? 0) >= (selector?.x ?? 0) + (selector?.width ?? 0)
    const below = (box?.y ?? 0) >= (selector?.y ?? 0) + (selector?.height ?? 0)
    expect(beside || below).toBe(true)
    await page
      .locator('.forecast-controls')
      .screenshot({ path: join(SHOTS, `skipped-days-${String(width)}.png`) })
    await page.getByRole('button', { name: 'Previous day' }).click()
    await expect(page.getByLabel('Delivery day')).toHaveValue('2026-11-19')
    await expect(note).toHaveCount(0)
    await expectNoSeriousAxe(page)
    await context.close()
  })
}

for (const width of [1440, 390]) {
  test(`places Retry below the message in every notice at ${String(width)} px`, async ({
    browser,
  }) => {
    const context = await browser.newContext({ viewport: { width, height: 900 } })
    const page = await context.newPage()
    await mockApi(page, {
      failing: ['/api/v1/forecasts', '/api/v1/backtest/metrics', '/api/v1/actuals'],
    })
    await page.goto('/')
    const alerts = page.getByRole('alert')
    // The client retries a 500 twice before it shows the error. The same failure behind
    // three sections is reported once, with one Retry.
    await expect(alerts).toHaveCount(1, { timeout: 20_000 })
    await expect(alerts).toContainText(
      'Could not load the forecast, the backtest and the generation data: the server answered with HTTP 500',
    )
    await expect(page.getByRole('button', { name: 'Retry' })).toHaveCount(1)
    for (const alert of await alerts.all()) {
      const text = await alert.locator('p').boundingBox()
      const button = await alert.getByRole('button', { name: 'Retry' }).boundingBox()
      const box = await alert.boundingBox()
      expect(text && button && box).toBeTruthy()
      if (text && button && box) {
        expect(button.y).toBeGreaterThanOrEqual(text.y + text.height)
        // Left aligned with the message in every notice.
        expect(Math.abs(button.x - text.x)).toBeLessThan(1)
      }
    }
    await page.screenshot({ path: join(SHOTS, `errors-${String(width)}.png`), fullPage: true })
    await expectNoSeriousAxe(page)
    await context.close()
  })
}

for (const width of [1440, 1024, 641, 390]) {
  test(`reserves the section heights while loading at ${String(width)} px`, async ({ browser }) => {
    const context = await browser.newContext({
      viewport: { width, height: 900 },
      timezoneId: 'Europe/Berlin',
    })
    const page = await context.newPage()
    let release: (value: undefined) => void = () => undefined
    const hold = new Promise<undefined>((done) => {
      release = done
    })
    await mockApi(page, { hold })
    await page.goto('/')
    await expect(page.getByText('Loading generation data')).toBeVisible()
    const heights = () =>
      page
        .locator('main > .section')
        .evaluateAll((list) => list.map((el) => Math.round(el.getBoundingClientRect().height)))
    const loading = await heights()
    await page.screenshot({ path: join(SHOTS, `loading-${String(width)}.png`), fullPage: true })
    release(undefined)
    await expect(page.locator('.chart-canvas canvas')).toHaveCount(4)
    const loaded = await heights()
    // Each section grows by less than a tenth of its final height once its data arrives.
    loaded.forEach((height, i) => {
      expect(height - (loading[i] ?? 0)).toBeLessThan(height * 0.1)
      expect(loading[i] ?? 0).toBeLessThanOrEqual(height)
    })
    await context.close()
  })
}

test('serves the page and its favicon under the production CSP without inline code', async ({
  request,
}) => {
  const page = await request.get('/')
  expect(page.status()).toBe(200)
  expect(page.headers()['content-security-policy']).toBe(CSP)
  const html = await page.text()
  expect(html).toContain('<link rel="icon" type="image/svg+xml" href="/favicon.svg" />')
  expect(html).toMatch(
    /<meta\s+name="description"\s+content="Day-ahead forecast of the German grid load/,
  )
  expect(html.match(/<meta name="theme-color"/g)).toHaveLength(2)
  // style-src 'self' and script-src 'self' block inline styles and inline scripts.
  expect(html).not.toMatch(/<style|\sstyle=/)
  expect(html).not.toMatch(/<script(?![^>]*\ssrc=)[^>]*>/)

  const icon = await request.get('/favicon.svg')
  expect(icon.status()).toBe(200)
  expect(icon.headers()['content-type']).toBe('image/svg+xml')
  expect(icon.headers()['content-security-policy']).toBe(CSP)
  const svg = await icon.text()
  expect(svg).toMatch(/^<svg /)
  expect(svg).not.toMatch(/<style|\sstyle=|<script/)
})

/** WCAG contrast of two computed colours, after resolving transparency against the page. */
function controlContrasts(page: Page) {
  return page.evaluate(() => {
    const rgb = (value: string) => (value.match(/[\d.]+/g) ?? []).map(Number)
    const lum = (value: string) => {
      const [r = 0, g = 0, b = 0] = rgb(value).map((c) => {
        const s = c / 255
        return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
      })
      return 0.2126 * r + 0.7152 * g + 0.0722 * b
    }
    const ratio = (a: string, b: string) => {
      const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x)
      return ((hi ?? 0) + 0.05) / ((lo ?? 0) + 0.05)
    }
    const page = getComputedStyle(document.body).backgroundColor
    const selector =
      '.button:not(:disabled), .day-select select, .theme-option span, .chart-data-toggle'
    return [...document.querySelectorAll(selector)].map((el) => {
      const style = getComputedStyle(el)
      const fill = style.backgroundColor
      return {
        control: el.textContent.trim(),
        againstFill: ratio(style.borderTopColor, fill),
        againstPage: ratio(style.borderTopColor, page),
      }
    })
  })
}

for (const scheme of ['light', 'dark'] as const) {
  test(`gives every control a border of at least 3:1 in ${scheme}`, async ({ browser }) => {
    const context = await browser.newContext({ colorScheme: scheme })
    const page = await context.newPage()
    await mockApi(page)
    await open(page)
    const contrasts = await controlContrasts(page)
    expect(contrasts.length).toBeGreaterThan(8)
    for (const c of contrasts) {
      // The checked theme option is filled with ink; its edge is the fill against the page.
      expect(Math.max(c.againstFill, c.againstPage), c.control).toBeGreaterThanOrEqual(3)
      expect(c.againstPage, c.control).toBeGreaterThanOrEqual(3)
    }
    await context.close()
  })
}
