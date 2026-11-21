import { screen, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'

import { NO_DATA } from './components/messages'
import { mockFetch, renderApp } from './test/api'
import type { Handler } from './test/api'
import * as fx from './test/fixtures'

vi.mock('./charts/Chart', () => ({
  Chart: ({ label, children }: { label: string; children?: ReactNode }) => (
    <figure role="img" aria-label={label}>
      {children}
    </figure>
  ),
}))

const ok = (body: unknown) => () => ({ body })

function routes(overrides: Record<string, Handler> = {}): Record<string, Handler> {
  return {
    '/api/v1/status': ok(fx.status()),
    '/api/v1/series': ok(fx.catalog()),
    '/api/v1/forecasts/days': ok({ days: fx.DAYS, limit: 400 }),
    '/api/v1/forecasts': (url) => ({
      body: fx.forecast(url.searchParams.get('delivery_day') ?? '2026-11-21', false),
    }),
    '/api/v1/backtest/metrics': ok(fx.backtest()),
    '/api/v1/actuals': (url) => ({
      body: fx.actuals((url.searchParams.get('series') ?? '').split(',')),
    }),
    ...overrides,
  }
}

describe('Page layout', () => {
  it('names the section in one h2 and sets the peak apart from the sentence', async () => {
    mockFetch(routes())
    const { container } = renderApp()
    const figure = await screen.findByText('62.7 GW at 23:00')
    expect(figure).toHaveClass('headline-figure')
    expect(figure.closest('p')).toHaveTextContent(
      'Forecast peak for Saturday 21 November: 62.7 GW at 23:00',
    )
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    const headings = [...container.querySelectorAll('h1, h2, h3')].map((h) => h.tagName)
    expect(headings.slice(0, 2)).toEqual(['H1', 'H2'])
    expect(screen.getByRole('heading', { level: 2, name: 'Day-ahead forecast' })).toHaveClass(
      'eyebrow',
    )
  })

  it('shows the backtest MAPE of each forecast next to the headline without a new request', async () => {
    const { calls } = mockFetch(routes())
    const { container } = renderApp()
    await screen.findByRole('table', { name: /Backtest errors/ })
    const band = container.querySelector('.key-figures')
    expect(band).not.toBeNull()
    if (band instanceof HTMLElement) {
      expect(band).toHaveTextContent('Backtest MAPE over 16,032 hours')
      const values = within(band)
        .getAllByRole('definition')
        .map((d) => d.textContent)
      // The same values as the overall MAPE column of the backtest table.
      expect(values).toEqual(['2.4 %', '5.9 %', 'n/a'])
    }
    expect(calls.filter((u) => u.pathname === '/api/v1/backtest/metrics')).toHaveLength(1)
  })

  it('announces old data across the page and keeps the header line plain', async () => {
    const base = fx.status()
    mockFetch(
      routes({
        '/api/v1/status': ok(
          fx.status({ series: base.series.map((s) => ({ ...s, age_seconds: 8 * 3600 })) }),
        ),
      }),
    )
    const { container } = renderApp()
    const banner = (await screen.findByText(/more than 6 hours old/)).closest('p')
    expect(banner).toHaveClass('stale-banner')
    expect(container.querySelector('main')?.firstElementChild).toBe(banner)
    expect(container.querySelector('.site-header .freshness')).toHaveTextContent(
      /^Data up to 20 Nov 2026, \d\d:\d\d$/,
    )
  })

  it('composes the first start as the forecast section without a freshness line', async () => {
    mockFetch(
      routes({
        '/api/v1/status': ok(
          fx.status({ initialised: false, snapshot_created_at: null, series: [] }),
        ),
      }),
    )
    const { container } = renderApp()
    await waitFor(() => {
      expect(container.querySelector('.empty-state')).toHaveTextContent(NO_DATA)
    })
    expect(screen.getByRole('heading', { level: 2, name: 'Day-ahead forecast' })).toBeVisible()
    expect(container.querySelector('.freshness-group')).toBeNull()
  })
})
