import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import type { Server } from 'node:http'
import { resolve } from 'node:path'

import { mockFixtureApi } from './mock.ts'
import { serveDist } from './server.ts'

/**
 * The draw-in of the forecast lines, in a real browser: the unit tests replace
 * ResizeObserver and the canvas, so they cannot see a redraw that cuts the animation short.
 */
const PORT = 8471
const DIST = resolve(import.meta.dirname, '..', 'dist')

let server: Server

test.beforeAll(async () => {
  server = await serveDist(DIST, PORT)
})

test.afterAll(async () => {
  await new Promise((done) => server.close(done))
})

interface Sample {
  t: number
  /** Drawn pixels of any kind: axes, grid and lines. */
  ink: number
  /** Dark pixels inside the plot, which only the forecast lines draw. */
  line: number
}

/**
 * Copies the right third of the forecast plot into a small canvas on every frame and keeps
 * each change of its ink. The lines reach that part last when they draw in from the left;
 * the grid there is pale, and the axis labels lie outside the copied part.
 */
function sampleForecastCanvas() {
  const samples: Sample[] = []
  ;(window as unknown as { __ink: Sample[] }).__ink = samples
  const probe = document.createElement('canvas')
  probe.width = 120
  probe.height = 80
  const reader = probe.getContext('2d', { willReadFrequently: true })
  const step = () => {
    const canvas = document.querySelector<HTMLCanvasElement>('.chart-forecast canvas')
    if (canvas && reader && canvas.width > 0) {
      const x = Math.floor((canvas.width * 2) / 3)
      const w = Math.floor(canvas.width * 0.28)
      const y = Math.floor(canvas.height * 0.1)
      const h = Math.floor(canvas.height * 0.72)
      reader.clearRect(0, 0, probe.width, probe.height)
      reader.drawImage(canvas, x, y, w, h, 0, 0, probe.width, probe.height)
      const data = reader.getImageData(0, 0, probe.width, probe.height).data
      let ink = 0
      let line = 0
      for (let i = 0; i < data.length; i += 4) {
        const alpha = data[i + 3] ?? 0
        if (alpha > 0) {
          ink += 1
          const r = data[i] ?? 0
          const g = data[i + 1] ?? 0
          const b = data[i + 2] ?? 0
          if (alpha > 64 && Math.max(r, g, b) < 190) {
            line += 1
          }
        }
      }
      const last = samples.at(-1)
      if (ink !== last?.ink || line !== last.line) {
        samples.push({ t: performance.now(), ink, line })
      }
    }
    requestAnimationFrame(step)
  }
  requestAnimationFrame(step)
}

async function inkOverTime(page: Page): Promise<Sample[]> {
  await page.addInitScript(sampleForecastCanvas)
  await mockFixtureApi(page)
  await page.goto(`http://127.0.0.1:${String(PORT)}/`)
  await expect(page.locator('.chart-forecast canvas')).toHaveCount(1)
  // The draw-in takes 700 ms; sample well past it.
  await page.waitForTimeout(2000)
  const samples = await page.evaluate(() => (window as unknown as { __ink: Sample[] }).__ink)
  // From the first frame that draws anything.
  return samples.slice(samples.findIndex((s) => s.ink > 0))
}

for (const width of [1440, 390]) {
  test(`draws the forecast lines in when motion is allowed at ${String(width)} px`, async ({
    browser,
  }) => {
    const context = await browser.newContext({
      colorScheme: 'light',
      reducedMotion: 'no-preference',
      viewport: { width, height: 900 },
    })
    try {
      const samples = await inkOverTime(await context.newPage())
      const lines = samples.map((s) => s.line)
      const first = lines[0] ?? 0
      const last = lines.at(-1) ?? 0
      // The lines are there in the end, but hardly in the first drawn frame: they rise
      // through several frames as they draw in from the left.
      expect(last).toBeGreaterThan(50)
      expect(first).toBeLessThan(last * 0.25)
      expect(new Set(lines).size).toBeGreaterThanOrEqual(3)
    } finally {
      await context.close()
    }
  })

  test(`shows the complete forecast chart at once with reduced motion at ${String(width)} px`, async ({
    browser,
  }) => {
    const context = await browser.newContext({
      colorScheme: 'light',
      reducedMotion: 'reduce',
      viewport: { width, height: 900 },
    })
    try {
      const samples = await inkOverTime(await context.newPage())
      const first = samples[0]?.line ?? 0
      const last = samples.at(-1)?.line ?? 0
      // A redraw once the web font has loaded may move a few pixels.
      expect(last).toBeGreaterThan(50)
      expect(first).toBeGreaterThan(last * 0.9)
    } finally {
      await context.close()
    }
  })
}
