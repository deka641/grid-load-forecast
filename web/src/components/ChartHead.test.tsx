import { screen } from '@testing-library/react'
import type { ReactNode } from 'react'

import { mockFetch, renderApp } from '../test/api'
import * as fx from '../test/fixtures'

vi.mock('../charts/Chart', () => ({
  Chart: ({ label, children }: { label: string; children?: ReactNode }) => (
    <figure role="img" aria-label={label}>
      {children}
    </figure>
  ),
}))

function serve(day: string) {
  mockFetch({
    '/api/v1/status': () => ({ body: fx.status() }),
    '/api/v1/series': () => ({ body: fx.catalog() }),
    '/api/v1/forecasts/days': () => ({ body: { days: [day], limit: 400 } }),
    '/api/v1/forecasts': () => ({ body: fx.forecast(day) }),
    '/api/v1/backtest/metrics': () => ({ body: fx.backtest() }),
    '/api/v1/actuals': (url) => ({
      body: fx.actuals((url.searchParams.get('series') ?? '').split(',')),
    }),
  })
}

describe('Head of the forecast chart', () => {
  it('puts the clock-change note with the legend directly above the chart', async () => {
    serve('2026-10-25')
    renderApp()
    const note = (await screen.findByText(/02:00 appears twice/)).closest('p')
    const head = note?.parentElement
    expect(head).toHaveClass('chart-head')
    expect(head?.firstElementChild).toBe(
      screen.getByRole('list', { name: 'Lines in the forecast chart' }),
    )
    expect(head?.nextElementSibling).toBe(
      screen.getByRole('img', { name: /Line chart of the grid load/ }),
    )
    // The dashed sample repeats the marker in the chart and is not read out.
    expect(note?.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
  })

  it('has only the legend on a day with 24 hours', async () => {
    serve('2026-11-20')
    renderApp()
    await screen.findByRole('list', { name: 'Lines in the forecast chart' })
    expect(document.querySelector('.chart-head')?.children).toHaveLength(1)
    expect(document.querySelector('.clock-change')).toBeNull()
  })
})
