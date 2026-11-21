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
  ink: number
}

/**
 * Counts the drawn pixels in the right third of the forecast canvas on every frame and keeps
 * each change. The lines reach that part last when they draw in from the left.
 */
function sampleForecastCanvas() {
  const samples: Sample[] = []
  ;(window as unknown as { __ink: Sample[] }).__ink = samples
  const step = () => {
    const canvas = document.querySelector<HTMLCanvasElement>('.chart-forecast canvas')
    const context = canvas?.getContext('2d')
    if (canvas && context && canvas.width > 0) {
      const left = Math.floor((canvas.width * 2) / 3)
      const data = context.getImageData(left, 0, canvas.width - left, canvas.height).data
      let ink = 0
      for (let i = 3; i < data.length; i += 4) {
        if ((data[i] ?? 0) > 0) {
          ink += 1
        }
      }
      if (ink !== samples.at(-1)?.ink) {
        samples.push({ t: performance.now(), ink })
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
  return samples.filter((s) => s.ink > 0)
}

for (const width of [1440, 390]) {
  test(`draws the forecast lines in when motion is allowed at ${String(width)} px`, async ({
    browser,
  }) => {
    const context = await browser.newContext({
      reducedMotion: 'no-preference',
      viewport: { width, height: 900 },
    })
    const samples = await inkOverTime(await context.newPage())
    const first = samples[0]
    const last = samples.at(-1)
    expect(first).toBeDefined()
    expect(last).toBeDefined()
    if (!first || !last) {
      return
    }
    // The first drawn frame lacks most of the lines on the right, which then fill in frame by
    // frame. With the easing, the right third fills during the later part of the 700 ms.
    expect(first.ink).toBeLessThan(last.ink * 0.8)
    expect(samples.length).toBeGreaterThan(5)
    expect(last.t - first.t).toBeGreaterThan(150)
    await context.close()
  })

  test(`shows the complete forecast chart at once with reduced motion at ${String(width)} px`, async ({
    browser,
  }) => {
    const context = await browser.newContext({
      reducedMotion: 'reduce',
      viewport: { width, height: 900 },
    })
    const samples = await inkOverTime(await context.newPage())
    const first = samples[0]
    const last = samples.at(-1)
    expect(first).toBeDefined()
    expect(last).toBeDefined()
    if (!first || !last) {
      return
    }
    // A redraw once the web font has loaded may move the axis labels by a few pixels.
    expect(first.ink).toBeGreaterThan(last.ink * 0.97)
    await context.close()
  })
}
