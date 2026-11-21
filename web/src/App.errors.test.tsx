import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'

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
const fail =
  (status: number, detail = 'the snapshot could not be read') =>
  () => ({ status, body: { detail } })

function routes(overrides: Record<string, Handler> = {}): Record<string, Handler> {
  return {
    '/api/v1/status': ok(fx.status()),
    '/api/v1/series': ok(fx.catalog()),
    '/api/v1/forecasts/days': ok({ days: fx.DAYS, limit: 400 }),
    '/api/v1/forecasts': (url) => ({
      body: fx.forecast(url.searchParams.get('delivery_day') ?? '2026-11-21'),
    }),
    '/api/v1/backtest/metrics': ok(fx.backtest()),
    '/api/v1/actuals': (url) => ({
      body: fx.actuals((url.searchParams.get('series') ?? '').split(',')),
    }),
    ...overrides,
  }
}

describe('One failure behind several sections', () => {
  it('shows a single notice with a single Retry that reloads every failed section', async () => {
    let healthy = false
    const snapshot =
      (good: Handler): Handler =>
      (url) =>
        healthy ? good(url) : fail(500)()
    const { calls } = mockFetch(
      routes({
        '/api/v1/forecasts': snapshot((url) => ({
          body: fx.forecast(url.searchParams.get('delivery_day') ?? '2026-11-21'),
        })),
        '/api/v1/backtest/metrics': snapshot(ok(fx.backtest())),
        '/api/v1/actuals': snapshot((url) => ({
          body: fx.actuals((url.searchParams.get('series') ?? '').split(',')),
        })),
      }),
    )
    renderApp()

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(
        'Could not load the forecast, the backtest and the generation data: the server answered with HTTP 500 (the snapshot could not be read).',
      )
    })
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: 'Retry' })).toHaveLength(1)
    // The sections keep their titles, so the page still shows its structure.
    expect(screen.getByRole('heading', { name: 'How accurate is it?' })).toBeInTheDocument()

    healthy = true
    const count = (path: string) => calls.filter((u) => u.pathname === path).length
    const before = ['/api/v1/forecasts', '/api/v1/backtest/metrics', '/api/v1/actuals'].map(count)
    fireEvent.click(within(screen.getByRole('alert')).getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText(/^Forecast peak for/)).toBeInTheDocument()
    expect(await screen.findByRole('table', { name: /Backtest errors/ })).toBeInTheDocument()
    expect(await screen.findByRole('table', { name: /Average generation/ })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
    ;['/api/v1/forecasts', '/api/v1/backtest/metrics', '/api/v1/actuals'].forEach((path, i) => {
      expect(count(path)).toBeGreaterThan(before[i] ?? 0)
    })
  })

  it('keeps separate notices when the sections fail for different reasons', async () => {
    mockFetch(
      routes({
        '/api/v1/forecasts': fail(500, 'one reason'),
        '/api/v1/backtest/metrics': fail(502, 'another reason'),
      }),
    )
    renderApp()
    await waitFor(() => {
      expect(screen.getAllByRole('alert')).toHaveLength(2)
    })
    expect(screen.getByText(/Could not load the forecast: .*HTTP 500/)).toBeInTheDocument()
    expect(screen.getByText(/Could not load the backtest: .*HTTP 502/)).toBeInTheDocument()
  })

  it('keeps a single failing section in its own notice', async () => {
    mockFetch(routes({ '/api/v1/backtest/metrics': fail(500) }))
    renderApp()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(
      'Could not load the backtest: the server answered with HTTP 500 (the snapshot could not be read).',
    )
    expect(await screen.findByText(/^Forecast peak for/)).toBeInTheDocument()
  })

  it('does not merge data states such as 404 with a failure', async () => {
    mockFetch(
      routes({
        '/api/v1/forecasts': fail(404, 'none'),
        '/api/v1/backtest/metrics': fail(404, 'none'),
        '/api/v1/actuals': fail(500),
      }),
    )
    renderApp()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Could not load the generation data')
    expect(screen.getByText(/^No backtest yet/)).toBeInTheDocument()
  })
})
