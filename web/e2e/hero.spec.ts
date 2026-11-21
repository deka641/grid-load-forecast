import { expect, test } from '@playwright/test'
import type { Browser, Page } from '@playwright/test'
import type { Server } from 'node:http'
import { resolve } from 'node:path'

import { mockFixtureApi } from './mock.ts'
import type { FixtureOptions } from './mock.ts'
import { serveDist } from './server.ts'

/** Placement of the forecast peak and the backtest key figures, and what moves when. */
const PORT = 8471
const DIST = resolve(import.meta.dirname, '..', 'dist')

let server: Server

test.beforeAll(async () => {
  server = await serveDist(DIST, PORT)
})

test.afterAll(async () => {
  await new Promise((done) => server.close(done))
})

interface Box {
  x: number
  y: number
  width: number
  height: number
}

async function box(page: Page, selector: string): Promise<Box> {
  const found = await page.locator(selector).boundingBox()
  expect(found, selector).not.toBeNull()
  return found ?? { x: 0, y: 0, width: 0, height: 0 }
}

function overlaps(a: Box, b: Box): boolean {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height
}

async function open(browser: Browser, width: number, options: FixtureOptions = {}) {
  const context = await browser.newContext({
    reducedMotion: 'reduce',
    viewport: { width, height: 900 },
  })
  const page = await context.newPage()
  await mockFixtureApi(page, options)
  await page.goto(`http://127.0.0.1:${String(PORT)}/`)
  await expect(page.locator('.headline')).toBeVisible()
  await expect(page.locator('.chart-forecast canvas')).toHaveCount(1)
  await page.evaluate(() => document.fonts.ready)
  return { context, page }
}

for (const width of [1100, 1024]) {
  test(`places the key figures beside the peak and the chart above the fold at ${String(width)} px`, async ({
    browser,
  }) => {
    const { context, page } = await open(browser, width)
    await expect(page.locator('.key-figures dd').first()).toHaveText('2.4 %')
    const headline = await box(page, '.headline')
    const figures = await box(page, '.key-figures')
    expect(figures.x).toBeGreaterThanOrEqual(headline.x + headline.width)
    expect(figures.y).toBeLessThan(headline.y + headline.height)
    const chart = await box(page, '.chart-forecast .chart-canvas')
    expect(chart.y + chart.height).toBeLessThanOrEqual(900)
    await context.close()
  })
}

for (const width of [1440, 1100, 1024, 900, 641, 390]) {
  test(`keeps the longest peak figure clear of the key figures at ${String(width)} px`, async ({
    browser,
  }) => {
    // 99.9 GW in the repeated 02:00 hour of the autumn clock change is the longest figure.
    const { context, page } = await open(browser, width, {
      peak: { day: '2026-10-25', index: 3, value: 99_900 },
    })
    await page.getByLabel('Delivery day').selectOption('2026-10-25')
    await expect(page.locator('.headline-figure')).toHaveText('99.9 GW at 02:00 (UTC+1)')
    await expect(page.locator('.key-figures dd').first()).toHaveText('2.4 %')
    expect(overlaps(await box(page, '.headline'), await box(page, '.key-figures'))).toBe(false)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
      width,
    )
    await context.close()
  })
}

for (const width of [1440, 1024, 900, 390]) {
  test(`keeps the chart in place when the backtest arrives after the forecast at ${String(width)} px`, async ({
    browser,
  }) => {
    let release: () => void = () => undefined
    const backtestHold = new Promise<void>((done) => (release = done))
    const { context, page } = await open(browser, width, { backtestHold })
    // The band holds its place without showing or announcing anything yet.
    const pending = page.locator('.key-figures')
    await expect(pending).toHaveAttribute('aria-hidden', 'true')
    await expect(pending).not.toBeVisible()
    const before = await box(page, '.chart-forecast .chart-canvas')
    const held = await box(page, '.key-figures')
    release()
    await expect(page.locator('.key-figures dd').first()).toHaveText('2.4 %')
    await expect(page.locator('.key-figures')).toBeVisible()
    const after = await box(page, '.chart-forecast .chart-canvas')
    expect(after.y).toBe(before.y)
    // About the same width too, so the values cannot push the band onto a new line.
    const shown = await box(page, '.key-figures')
    expect(Math.abs(shown.width - held.width)).toBeLessThanOrEqual(4)
    expect(shown.height).toBe(held.height)
    await context.close()
  })
}

test('gives the space of the key figures back when the backtest fails', async ({ browser }) => {
  const { context, page } = await open(browser, 900, { backtestFails: true })
  await expect(page.getByRole('alert')).toContainText('Could not load the backtest', {
    timeout: 20_000,
  })
  await expect(page.locator('.key-figures')).toHaveCount(0)
  const hero = await box(page, '.forecast-hero')
  const headline = await box(page, '.headline')
  expect(hero.height).toBe(headline.height)
  await context.close()
})
