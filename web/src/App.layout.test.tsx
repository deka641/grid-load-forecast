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

const never = () => new Promise<never>(() => undefined)

describe('Places held while loading', () => {
  it('holds the header line and the forecast layout while the status loads', () => {
    mockFetch(routes({ '/api/v1/status': never }))
    const { container } = renderApp()
    // The notice stays readable: it is not inside a hidden placeholder.
    const notice = screen.getByRole('status')
    expect(notice).toHaveTextContent('Loading status')
    expect(notice.closest('[aria-hidden="true"]')).toBeNull()
    expect(notice.parentElement).toHaveClass('loading-forecast')

    // An empty, hidden line holds the place of the data time.
    const line = container.querySelector('.site-meta .freshness-pending')
    expect(line).toHaveAttribute('aria-hidden', 'true')
    expect(line?.textContent).toBe('')
    expect(screen.queryByText(/Data up to/)).toBeNull()
    const page = container.querySelector('.loading-page')
    expect(page?.querySelector('.skeleton-hero')).toHaveAttribute('aria-hidden', 'true')
    expect(page?.querySelector('.forecast-controls .skeleton-controls')).not.toBeNull()
    expect(page?.querySelector('.skeleton-prose')).not.toBeNull()
    // No glow and no key figures to read: the band is an invisible copy.
    expect(page?.querySelector('.key-figures')).toHaveClass('key-figures-pending')
  })

  it('drops the held header line once the status arrives', async () => {
    mockFetch(routes())
    const { container } = renderApp()
    expect(await screen.findByText('Data up to 20 Nov 2026, 13:30')).toBeInTheDocument()
    expect(container.querySelector('.freshness-pending')).toBeNull()
  })

  it('holds the headline and the day selector in their slots while the forecast loads', async () => {
    mockFetch(routes({ '/api/v1/forecasts/days': never, '/api/v1/forecasts': never }))
    const { container } = renderApp()
    await screen.findByText('Loading forecast')
    const section = screen.getByRole('heading', { name: 'Day-ahead forecast' }).parentElement
    const hero = section?.querySelector(':scope > .forecast-hero')
    expect(hero).toHaveClass('skeleton-hero')
    expect(section?.querySelector('.forecast-controls > .skeleton-controls')).not.toBeNull()
    expect(container.querySelector('.headline-figure')?.textContent).toBe('')
  })

  it('shows the day selector beside the held headline once the days are known', async () => {
    mockFetch(routes({ '/api/v1/forecasts': never }))
    const { container } = renderApp()
    expect(await screen.findByLabelText('Delivery day')).toHaveValue('2026-11-21')
    expect(container.querySelector('.skeleton-controls')).toBeNull()
    expect(container.querySelector('.skeleton-hero')).not.toBeNull()
    await waitFor(() => {
      expect(screen.getByText('Loading forecast')).toBeInTheDocument()
    })
  })
})

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
      // No shared hour count: the SMARD forecast is scored on fewer hours than the others.
      expect(band.querySelector('.key-figures-label')?.textContent).toBe('Backtest MAPE')
      expect(band).not.toHaveTextContent(/hours/)
      const values = within(band)
        .getAllByRole('definition')
        .map((d) => d.textContent)
      // The same values as the overall MAPE column of the backtest table.
      expect(values).toEqual(['2.4 %', '5.9 %', 'n/a'])
    }
    expect(calls.filter((u) => u.pathname === '/api/v1/backtest/metrics')).toHaveLength(1)
  })

  it('announces old data across the page and names its time only there', async () => {
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
    expect(banner).toHaveTextContent(
      /^Data up to 20 Nov 2026, \d\d:\d\d \(more than 6 hours old\)$/,
    )
    expect(screen.getAllByText(/Data up to/)).toHaveLength(1)
    // Without a time of its own, the header holds the place of the line empty and hidden.
    expect(container.querySelector('.site-meta .freshness-pending')).toHaveAttribute(
      'aria-hidden',
      'true',
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
    expect(screen.queryByText(/Data up to/)).toBeNull()
    expect(container.querySelector('.site-meta .freshness-pending')).toHaveAttribute(
      'aria-hidden',
      'true',
    )
  })
})
