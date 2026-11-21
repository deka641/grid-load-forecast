import { screen } from '@testing-library/react'
import type { ReactNode } from 'react'

import type { Actuals } from '../api/client'
import { mockFetch, renderApp } from '../test/api'
import * as fx from '../test/fixtures'

vi.mock('../charts/Chart', () => ({
  Chart: ({ label, children }: { label: string; children?: ReactNode }) => (
    <figure role="img" aria-label={label}>
      {children}
    </figure>
  ),
}))

const STAMPS = [
  '2026-11-19T22:00:00+01:00',
  '2026-11-19T23:00:00+01:00',
  '2026-11-20T00:00:00+01:00',
  '2026-11-20T01:00:00+01:00',
]

function actuals(prices: (number | null)[]): Actuals {
  const point = (ts: string, value: number) => ({ ts, value })
  return {
    resolution: 'hour',
    start: '2026-11-14',
    end: '2026-11-20',
    series: [
      {
        key: 'gen_solar',
        label: 'Photovoltaics',
        unit: 'MWh',
        points: STAMPS.map((ts) => point(ts, 1000)),
      },
      {
        key: 'price_day_ahead',
        label: 'Day-ahead price DE/LU',
        unit: 'EUR/MWh',
        points: STAMPS.flatMap((ts, i) => {
          const value = prices[i] ?? null
          return value === null ? [] : [point(ts, value)]
        }),
      },
    ],
  }
}

function serve(prices: (number | null)[]) {
  mockFetch({
    '/api/v1/status': () => ({ body: fx.status() }),
    '/api/v1/series': () => ({ body: fx.catalog() }),
    '/api/v1/forecasts/days': () => ({ body: { days: fx.DAYS, limit: 400 } }),
    '/api/v1/forecasts': () => ({ body: fx.forecast('2026-11-21', false) }),
    '/api/v1/backtest/metrics': () => ({ body: fx.backtest() }),
    '/api/v1/actuals': () => ({ body: actuals(prices) }),
  })
}

describe('Price summary', () => {
  it('describes hourly means once and names the last published price', async () => {
    serve([-10, 30, null, null])
    renderApp()
    const summary = await screen.findByText(/^Hourly prices/)
    expect(summary).toHaveTextContent(
      'Hourly prices (each the mean of four quarter hours) from -10.00 to 30.00 EUR/MWh, ' +
        'average 10.00 EUR/MWh, negative in 1 of 2 hours. A single negative quarter hour can ' +
        'be hidden in its hourly mean. The last published price is for Thu 19 Nov, 23:00 ' +
        '(UTC+1); the shaded part of the chart has no prices yet.',
    )
    expect(summary.textContent.match(/mean/g)).toHaveLength(2)
  })

  it.each([[[10, 20, 30, 40]], [[10, 20, 30, null]]])(
    'says nothing about the end when the chart shades nothing (%j)',
    async (prices) => {
      serve(prices)
      renderApp()
      const summary = await screen.findByText(/^Hourly prices/)
      expect(summary).not.toHaveTextContent('last published price')
      expect(summary).not.toHaveTextContent('shaded')
    },
  )
})
