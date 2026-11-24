import type { Page, Route } from '@playwright/test'

import * as fixtures from './fixtures.ts'

export interface FixtureOptions {
  /** The status request waits for this before it answers. */
  statusHold?: Promise<unknown>
  /** The status answer in place of the fresh fixture status. */
  status?: unknown
  /** The status request answers 500. */
  statusFails?: boolean
  /** Every data request (all but status and series) waits for this before it answers. */
  dataHold?: Promise<unknown>
  /** The backtest request waits for this before it answers. */
  backtestHold?: Promise<unknown>
  /** The backtest request answers 500. */
  backtestFails?: boolean
  /** Sets the model forecast of one hour of a day, in MWh. */
  peak?: { day: string; index: number; value: number }
}

/** Answers the API requests of the page with the fixture data. */
export async function mockFixtureApi(page: Page, options: FixtureOptions = {}) {
  await page.route('**/api/v1/**', async (route: Route) => {
    const url = new URL(route.request().url())
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (url.pathname === '/api/v1/status') {
      await options.statusHold
    } else if (url.pathname !== '/api/v1/series') {
      await options.dataHold
    }
    switch (url.pathname) {
      case '/api/v1/status':
        if (options.statusFails) {
          return json({ detail: 'the snapshot could not be read' }, 500)
        }
        return json(options.status ?? fixtures.status())
      case '/api/v1/series':
        return json(fixtures.seriesCatalog(true))
      case '/api/v1/forecasts/days':
        return json({ days: fixtures.forecastDays(), limit: 400 })
      case '/api/v1/forecasts': {
        const day = url.searchParams.get('delivery_day') ?? fixtures.LATEST_DAY
        const body = fixtures.forecastDay(day)
        const point = options.peak?.day === day ? body.points[options.peak.index] : undefined
        if (point && options.peak) {
          point.lightgbm = options.peak.value
        }
        return json(body)
      }
      case '/api/v1/backtest/metrics':
        await options.backtestHold
        if (options.backtestFails) {
          return json({ detail: 'the snapshot could not be read' }, 500)
        }
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
