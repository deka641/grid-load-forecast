import { screen, waitFor } from '@testing-library/react'
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
const fail = () => ({ status: 500, body: { detail: 'the snapshot could not be read' } })

function routes(overrides: Record<string, Handler>): Record<string, Handler> {
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

function sectionOf(name: string | RegExp): HTMLElement {
  const section = screen.getByRole('heading', { level: 2, name }).closest('section')
  if (!section) {
    throw new Error('no section')
  }
  return section
}

describe('Sections behind one shared error notice', () => {
  it('each show one quiet line that points to the notice instead of ending at the title', async () => {
    mockFetch(
      routes({
        '/api/v1/forecasts': fail,
        '/api/v1/backtest/metrics': fail,
        '/api/v1/actuals': fail,
      }),
    )
    renderApp()
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(
        'Could not load the forecast, the backtest and the generation data',
      )
    })
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    const lines: [string | RegExp, string][] = [
      ['Day-ahead forecast', 'Could not load the forecast; see the notice above.'],
      ['How accurate is it?', 'Could not load the backtest; see the notice above.'],
      [/^Context:/, 'Could not load the generation data; see the notice above.'],
    ]
    for (const [heading, text] of lines) {
      const line = sectionOf(heading).querySelector('.shared-failure')
      expect(line).toHaveTextContent(text)
      expect(line).not.toHaveAttribute('role')
    }
    // The context section does not describe a chart it cannot show.
    expect(sectionOf(/^Context:/)).not.toHaveTextContent('Hourly electricity generation')
  })

  it('keeps the context description when only other sections fail', async () => {
    mockFetch(routes({ '/api/v1/forecasts': fail, '/api/v1/backtest/metrics': fail }))
    renderApp()
    await screen.findByRole('table', { name: /Average generation/ })
    expect(sectionOf(/^Context:/)).toHaveTextContent('Hourly electricity generation')
    expect(sectionOf(/^Context:/).querySelector('.shared-failure')).toBeNull()
  })
})
