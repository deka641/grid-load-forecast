import { screen } from '@testing-library/react'
import type { ReactNode } from 'react'

import type { Actuals, Status } from '../api/client'
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

type Prices = (number | null)[]

function actuals(prices: Prices, genStamps = STAMPS, priceStamps = genStamps): Actuals {
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
        points: genStamps.map((ts) => point(ts, 1000)),
      },
      {
        key: 'price_day_ahead',
        label: 'Day-ahead price DE/LU',
        unit: 'EUR/MWh',
        points: priceStamps.flatMap((ts, i) => {
          const value = prices[i] ?? null
          return value === null ? [] : [point(ts, value)]
        }),
      },
    ],
  }
}

function serve(body: Actuals, status: Partial<Status> = {}) {
  mockFetch({
    '/api/v1/status': () => ({ body: fx.status(status) }),
    '/api/v1/series': () => ({ body: fx.catalog() }),
    '/api/v1/forecasts/days': () => ({ body: { days: fx.DAYS, limit: 400 } }),
    '/api/v1/forecasts': () => ({ body: fx.forecast('2026-11-21', false) }),
    '/api/v1/backtest/metrics': () => ({ body: fx.backtest() }),
    '/api/v1/actuals': () => ({ body }),
  })
}

const pad = (n: number) => String(n).padStart(2, '0')
const hoursOf = (day: string, from: number, to: number, offset = '+01:00') =>
  Array.from({ length: to - from + 1 }, (_, i) => `${day}T${pad(from + i)}:00:00${offset}`)

function priceRows(): string[] {
  const chart = screen.getByRole('img', { name: /^Line chart of the hourly day-ahead/ })
  return [...chart.querySelectorAll('tbody th')].map((th) => th.textContent)
}

describe('Price summary', () => {
  it('describes hourly means once and names the last published price', async () => {
    // The page is loaded on 21 Nov, but the newest snapshot is from 20 Nov 13:56, when the
    // prices of 21 Nov were not yet due.
    const stamps = [...STAMPS.slice(0, 2), ...hoursOf('2026-11-21', 0, 1)]
    serve(actuals([-10, 30, null, null], stamps), { generated_at: '2026-11-21T10:00:00+01:00' })
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
      serve(actuals(prices))
      renderApp()
      const summary = await screen.findByText(/^Hourly prices/)
      expect(summary).not.toHaveTextContent('last published price')
      expect(summary).not.toHaveTextContent('shaded')
      expect(summary).not.toHaveTextContent('SMARD has published no prices')
    },
  )

  it('names the hours SMARD has not priced inside the range', async () => {
    serve(actuals([-10, null, null, 30]))
    renderApp()
    const summary = await screen.findByText(/^Hourly prices/)
    expect(summary).toHaveTextContent(
      'Hourly prices (each the mean of four quarter hours) from -10.00 to 30.00 EUR/MWh, ' +
        'average 10.00 EUR/MWh over 2 of 4 hours, negative in 1 of them. A single negative ' +
        'quarter hour can be hidden in its hourly mean. SMARD has published no prices for ' +
        'Thu 19 Nov, 23:00 to Fri 20 Nov, 00:00 (UTC+1); the chart shades these hours lighter.',
    )
    expect(summary).not.toHaveTextContent('shaded part')
  })

  it('treats a missing end as a source gap once the prices are overdue', async () => {
    // The same hours as in the first case, but the snapshot is from 21 Nov 19:30, when the
    // prices of 21 Nov had long been due.
    const stamps = [...STAMPS.slice(0, 2), ...hoursOf('2026-11-21', 0, 1)]
    serve(actuals([-10, 30, null, null], stamps), {
      generated_at: '2026-11-21T19:35:00+01:00',
      snapshot_created_at: '2026-11-21T19:30:00+01:00',
    })
    renderApp()
    const summary = await screen.findByText(/^Hourly prices/)
    expect(summary).toHaveTextContent(
      'average 10.00 EUR/MWh over 2 of 4 hours, negative in 1 of them.',
    )
    expect(summary).toHaveTextContent(
      'SMARD has published no prices for Sat 21 Nov, 00:00 to Sat 21 Nov, 01:00 (UTC+1); the chart shades these hours lighter.',
    )
    expect(summary).not.toHaveTextContent('last published price')
  })

  it('names a gap over the 25-hour day with both offsets', async () => {
    const stamps = [
      '2026-10-24T23:00:00+02:00',
      ...hoursOf('2026-10-25', 0, 2, '+02:00'),
      ...hoursOf('2026-10-25', 2, 23),
      '2026-10-26T00:00:00+01:00',
    ]
    serve(actuals([10, ...new Array<null>(25).fill(null), 30], stamps))
    renderApp()
    const summary = await screen.findByText(/^Hourly prices/)
    expect(summary).toHaveTextContent(
      'average 20.00 EUR/MWh over 2 of 27 hours, negative in 0 of them.',
    )
    expect(summary).toHaveTextContent(
      'SMARD has published no prices for Sun 25 Oct, 00:00 (UTC+2) to Sun 25 Oct, 23:00 (UTC+1); the chart shades these hours lighter.',
    )
  })

  it('shows the published prices for the rest of today', async () => {
    // Generation ends at 20 Nov 01:00, prices run to 03:00; 21 Nov is after today.
    const priceStamps = [...STAMPS, ...hoursOf('2026-11-20', 2, 3), '2026-11-21T00:00:00+01:00']
    serve(actuals([1, 2, 3, 4, 5, 6, 7], STAMPS, priceStamps))
    renderApp()
    const summary = await screen.findByText(/^Hourly prices/)
    expect(summary).toHaveTextContent('from 1.00 to 6.00 EUR/MWh')
    expect(summary).toHaveTextContent('negative in 0 of 6 hours.')
    expect(priceRows()).toEqual([
      'Thu 19 Nov, 22:00 (UTC+1)',
      'Thu 19 Nov, 23:00 (UTC+1)',
      'Fri 20 Nov, 00:00 (UTC+1)',
      'Fri 20 Nov, 01:00 (UTC+1)',
      'Fri 20 Nov, 02:00 (UTC+1)',
      'Fri 20 Nov, 03:00 (UTC+1)',
    ])
    // The generation chart keeps its own hours.
    const mix = screen.getByRole('img', { name: /^Stacked area chart/ })
    expect(mix).toBeInTheDocument()
    expect(screen.getByText(/^Hourly electricity generation/)).toHaveTextContent(
      'and the day-ahead wholesale price for every hour SMARD has published up to the end of today',
    )
  })
})
