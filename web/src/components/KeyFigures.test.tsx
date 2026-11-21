import { screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'

import { mockFetch, renderApp } from '../test/api'
import type { Handler } from '../test/api'
import * as fx from '../test/fixtures'

vi.mock('../charts/Chart', () => ({
  Chart: ({ label, children }: { label: string; children?: ReactNode }) => (
    <figure role="img" aria-label={label}>
      {children}
    </figure>
  ),
}))

function serve(backtest: Handler) {
  mockFetch({
    '/api/v1/status': () => ({ body: fx.status() }),
    '/api/v1/series': () => ({ body: fx.catalog() }),
    '/api/v1/forecasts/days': () => ({ body: { days: fx.DAYS, limit: 400 } }),
    '/api/v1/forecasts': () => ({ body: fx.forecast('2026-11-21', false) }),
    '/api/v1/backtest/metrics': backtest,
    '/api/v1/actuals': (url) => ({
      body: fx.actuals((url.searchParams.get('series') ?? '').split(',')),
    }),
  })
}

describe('Key figures next to the forecast peak', () => {
  it('holds their place without values while the backtest loads', async () => {
    serve(() => new Promise<never>(() => undefined))
    const { container } = renderApp()
    await screen.findByText('62.7 GW at 23:00')
    const band = container.querySelector('.key-figures')
    expect(band).toHaveClass('key-figures-pending')
    expect(band).toHaveAttribute('aria-hidden', 'true')
    expect(band).not.toHaveTextContent('%')
    expect(screen.queryByRole('definition')).toBeNull()
  })

  it('leaves no space for them when the backtest fails', async () => {
    serve(() => ({ status: 500, body: { detail: 'broken' } }))
    const { container } = renderApp()
    await screen.findByText(/Could not load the backtest/)
    await waitFor(() => {
      expect(container.querySelector('.forecast-hero')).not.toBeNull()
    })
    expect(container.querySelector('.key-figures')).toBeNull()
  })
})
