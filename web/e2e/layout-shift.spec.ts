import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import type { Server } from 'node:http'
import { resolve } from 'node:path'

import * as fixtures from './fixtures.ts'
import { mockFixtureApi } from './mock.ts'
import { serveDist } from './server.ts'

/** The page must not move when the loading placeholders give way to the content. */
const PORT = 8471
const DIST = resolve(import.meta.dirname, '..', 'dist')
/** The "good" limit of the Core Web Vitals. */
const LIMIT = 0.1

let server: Server

test.beforeAll(async () => {
  server = await serveDist(DIST, PORT)
})

test.afterAll(async () => {
  await new Promise((done) => server.close(done))
})

interface Shift {
  value: number
  sources: string[]
}

/** Record layout shifts from before the page runs any code of its own. */
async function recordShifts(page: Page) {
  await page.addInitScript(() => {
    const store: { value: number; sources: string[] }[] = []
    ;(window as unknown as { __shifts: typeof store }).__shifts = store
    const name = (node: Node | null) => {
      if (!(node instanceof Element)) {
        return String(node?.nodeName)
      }
      const classes = [...node.classList].slice(0, 2).join('.')
      const tag = node.tagName.toLowerCase()
      return classes === '' ? tag : `${tag}.${classes}`
    }
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        const shift = entry as PerformanceEntry & {
          value: number
          hadRecentInput: boolean
          sources?: { node: Node | null }[]
        }
        if (!shift.hadRecentInput) {
          store.push({
            value: Number(shift.value.toFixed(4)),
            sources: (shift.sources ?? []).map((source) => name(source.node)),
          })
        }
      }
    }).observe({ type: 'layout-shift', buffered: true })
  })
}

async function shifts(page: Page): Promise<Shift[]> {
  return page.evaluate(() => (window as unknown as { __shifts: Shift[] }).__shifts)
}

function hold() {
  let release: () => void = () => undefined
  const promise = new Promise<void>((done) => {
    release = done
  })
  return { promise, release }
}

/** Two frames, so that a layout change is painted and reported. */
async function settle(page: Page) {
  await page.evaluate(
    () =>
      new Promise((done) => {
        requestAnimationFrame(() => requestAnimationFrame(done))
      }),
  )
}

type Path = 'fresh' | 'stale' | 'first start' | 'failed status'

const CASES: readonly { path: Path; timing: 'delayed' | 'instant' }[] = [
  { path: 'fresh', timing: 'delayed' },
  { path: 'fresh', timing: 'instant' },
  { path: 'stale', timing: 'delayed' },
  { path: 'first start', timing: 'delayed' },
  { path: 'failed status', timing: 'delayed' },
]

/** What the page shows once it has loaded on each path. */
async function loaded(page: Page, path: Path) {
  if (path === 'first start') {
    await expect(page.locator('.empty-state')).toBeVisible()
    return
  }
  if (path === 'failed status') {
    // The status query retries twice before it gives up.
    await expect(page.getByText(/^Could not load the status/)).toBeVisible({ timeout: 20_000 })
    return
  }
  if (path === 'stale') {
    await expect(page.locator('.stale-banner')).toBeVisible()
  }
  await expect(page.locator('.headline')).toBeVisible()
  await expect(page.locator('.chart-canvas canvas')).toHaveCount(4)
}

for (const scheme of ['light', 'dark'] as const) {
  for (const width of [1440, 1024, 390]) {
    for (const { path, timing } of CASES) {
      test(`keeps the layout in place while the page loads on the ${path} path with ${timing} answers in ${scheme} at ${String(width)} px`, async ({
        browser,
      }) => {
        const context = await browser.newContext({
          colorScheme: scheme,
          viewport: { width, height: 900 },
          deviceScaleFactor: width < 600 ? 2 : 1,
          timezoneId: 'Europe/Berlin',
        })
        const page = await context.newPage()
        const status = hold()
        const data = hold()
        await recordShifts(page)
        await mockFixtureApi(page, {
          ...(timing === 'delayed' ? { statusHold: status.promise, dataHold: data.promise } : {}),
          ...(path === 'stale' ? { status: fixtures.status(8 * 3600) } : {}),
          ...(path === 'first start' ? { status: fixtures.statusBeforeFirstSnapshot() } : {}),
          statusFails: path === 'failed status',
        })
        await page.goto(`http://127.0.0.1:${String(PORT)}/`)

        if (timing === 'delayed') {
          // First the status arrives, then the data of the sections, each after a wait.
          // No screenshots here: a full page capture resizes the viewport, and the browser
          // then leaves out the shifts that follow.
          await expect(page.getByText('Loading status')).toBeVisible()
          await page.evaluate(() => document.fonts.ready)
          await settle(page)
          status.release()
          if (path === 'fresh' || path === 'stale') {
            await expect(page.getByText('Loading generation data')).toBeVisible()
            await settle(page)
          }
          data.release()
        }
        await loaded(page, path)
        await page.evaluate(() => document.fonts.ready)
        await settle(page)

        const recorded = await shifts(page)
        // All shifts added up, which is never less than the windowed CLS of the browser.
        const total = recorded.reduce((sum, shift) => sum + shift.value, 0)
        test.info().annotations.push({
          type: 'layout shift',
          description: `${total.toFixed(4)} ${JSON.stringify(recorded)}`,
        })
        expect(total, JSON.stringify(recorded)).toBeLessThan(LIMIT)
        await context.close()
      })
    }
  }
}
