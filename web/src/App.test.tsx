import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'

import { NO_BACKTEST, NO_DATA, NO_FORECAST, SNAPSHOT_FALLBACK } from './components/messages'
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

const MISSING = 'no data snapshot is available yet; the pipeline has not published one'
const ok = (body: unknown) => () => ({ body })
const fail =
  (status: number, detail = 'x') =>
  () => ({ status, body: { detail } })

function routes(overrides: Record<string, Handler> = {}): Record<string, Handler> {
  return {
    '/api/v1/status': ok(fx.status()),
    '/api/v1/series': ok(fx.catalog()),
    '/api/v1/forecasts/days': ok({ days: fx.DAYS, limit: 400 }),
    '/api/v1/forecasts': (url) => {
      const day = url.searchParams.get('delivery_day') ?? '2026-11-21'
      return { body: fx.forecast(day, day !== '2026-11-21') }
    },
    '/api/v1/backtest/metrics': ok(fx.backtest()),
    '/api/v1/actuals': (url) => ({
      body: fx.actuals((url.searchParams.get('series') ?? '').split(',')),
    }),
    ...overrides,
  }
}

const headline = () => screen.findByText(/^Forecast peak for/)

describe('Dashboard with data', () => {
  it('shows the headline, freshness, backtest, context and footer', async () => {
    const { calls } = mockFetch(routes())
    renderApp()
    expect(screen.getByRole('status')).toHaveTextContent('Loading status')

    expect(await headline()).toHaveTextContent(
      'Forecast peak for Saturday 21 November: 62.7 GW at 23:00',
    )
    expect(screen.getByText('Data up to 20 Nov 2026, 13:30')).toBeInTheDocument()
    expect(screen.getByText(/No actual load published yet/)).toBeInTheDocument()

    const table = await screen.findByRole('table', { name: /Backtest errors/ })
    const rows = within(table).getAllByRole('row')
    expect(rows[2]).toHaveTextContent('LightGBM modelLightGBM1.20 GW16,0322.4 %2.2 %2.6 %n/a')
    expect(rows[3]).toHaveTextContent('Workdays 2.2 %, Saturdays 2.6 %, Sundays and holidays n/a')
    expect(rows[6]).toHaveTextContent('SMARD forecastSMARD forecastn/an/an/an/an/an/a')
    expect(screen.getByText(/Delivery days from 6 Jan 2025 to 15 Nov 2026/)).toHaveTextContent(
      'retrained every 7 days on all earlier complete days. Errors are measured against the actual grid load over 16,032 hours.',
    )
    expect(screen.queryByText(/hours that also have a SMARD forecast/)).toBeNull()

    expect(await screen.findByText(/HEDWIG procedure/)).toBeInTheDocument()
    expect(screen.getByText(/negative in 1 of 2 hours/)).toBeInTheDocument()
    expect(
      screen.getByRole('img', { name: /Stacked area chart.*14 Nov 2026 to 20 Nov 2026/ }),
    ).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Context: the past week' })).toBeInTheDocument()
    const actualsCall = calls.find((u) => u.pathname === '/api/v1/actuals')
    expect(actualsCall?.searchParams.get('series')).toBe(
      'gen_wind_onshore,gen_solar,price_day_ahead',
    )
    expect(actualsCall?.searchParams.get('start')).toBe('2026-11-14')
    expect(actualsCall?.searchParams.get('end')).toBe('2026-11-20')

    expect(
      screen.getByText('Last pipeline run: succeeded (incremental), finished 20 Nov 2026, 13:51'),
    ).toBeInTheDocument()
    expect(screen.getByText('Snapshot: 20 Nov 2026, 13:56')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Source code' })).toHaveAttribute(
      'href',
      'https://github.com/deka641/grid-load-forecast',
    )
    expect(screen.getByRole('link', { name: 'CC BY 4.0' })).toHaveAttribute(
      'href',
      'https://creativecommons.org/licenses/by/4.0/',
    )
  })

  it('computes this day errors client-side and walks through the days', async () => {
    mockFetch(routes())
    renderApp()
    await headline()
    const next = screen.getByRole('button', { name: 'Next day' })
    expect(next).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: 'Previous day' }))
    expect(await screen.findByText(/Friday 20 November/)).toBeInTheDocument()
    const errors = screen.getByText('This day').parentElement
    expect(errors).toHaveTextContent('LightGBM2.0 %Baseline5.0 %SMARD forecast0.0 %')
    expect(errors).toHaveTextContent('over 24 of 24 hours')

    fireEvent.click(screen.getByRole('button', { name: 'Next day' }))
    expect(await screen.findByText(/Saturday 21 November/)).toBeInTheDocument()
  })

  it('counts SMARD hours separately when they differ', async () => {
    const day = fx.forecast('2026-11-20')
    day.points = day.points.map((p, i) => (i < 4 ? { ...p, tso: null } : p))
    mockFetch(routes({ '/api/v1/forecasts': ok(day) }))
    renderApp()
    expect(
      await screen.findByText(
        'MAPE against the actual load over 24 of 24 hours; SMARD forecast over 20 hours',
      ),
    ).toBeInTheDocument()
  })

  it('handles the 25 and 23 hour clock-change days', async () => {
    mockFetch(routes())
    renderApp()
    await headline()
    const select = screen.getByLabelText('Delivery day')
    fireEvent.change(select, { target: { value: '2026-10-25' } })
    expect(await screen.findByText(/02:00 appears twice/)).toHaveTextContent('25 hours')
    const hidden = screen.getByRole('table', { name: /Hourly load for Sun 25 Oct 2026/ })
    expect(within(hidden).getByText('02:00 (UTC+2)')).toBeInTheDocument()
    expect(within(hidden).getByText('02:00 (UTC+1)')).toBeInTheDocument()

    fireEvent.change(select, { target: { value: '2026-03-29' } })
    expect(await screen.findByText(/02:00 does not exist/)).toHaveTextContent('23 hours')
    expect(screen.getByRole('button', { name: 'Previous day' })).toBeDisabled()
  })

  it('lists the shown day even if the day list does not contain it', async () => {
    mockFetch(routes({ '/api/v1/forecasts/days': ok({ days: [], limit: 400 }) }))
    renderApp()
    await headline()
    expect(screen.getByRole('option', { name: 'Sat 21 Nov 2026' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous day' })).toBeDisabled()
  })
})

describe('Dashboard states', () => {
  it('shows the first-start message when no snapshot exists', async () => {
    // What the API answers before the first snapshot: 200, not initialised, no runs.
    const { calls } = mockFetch(
      routes({
        '/api/v1/status': ok(
          fx.status({
            initialised: false,
            snapshot_created_at: null,
            series: [],
            last_load_run: null,
          }),
        ),
      }),
    )
    renderApp()
    expect(await screen.findByText(NO_DATA)).toBeInTheDocument()
    expect(calls.map((u) => u.pathname)).toEqual(['/api/v1/status'])
    expect(screen.getByText('Last pipeline run: none yet')).toBeInTheDocument()
  })

  it('reports an unreadable snapshot with retry instead of the first-start text', async () => {
    let attempts = 0
    mockFetch(
      routes({
        '/api/v1/status': () => {
          attempts += 1
          return {
            body:
              attempts === 1
                ? fx.status({
                    initialised: false,
                    snapshot_created_at: null,
                    snapshot_error: 'the data snapshot cannot be opened',
                    last_load_run: null,
                  })
                : fx.status(),
          }
        },
      }),
    )
    renderApp()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(
      'The data snapshot is not available: the data snapshot cannot be opened.',
    )
    expect(screen.queryByText(NO_DATA)).toBeNull()
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }))
    expect(await headline()).toBeInTheDocument()
  })

  it('notes when an older snapshot is served because the newest cannot be read', async () => {
    mockFetch(
      routes({
        '/api/v1/status': ok(
          fx.status({ snapshot_error: 'the newest data snapshot file cannot be opened' }),
        ),
      }),
    )
    renderApp()
    expect(await screen.findByText(SNAPSHOT_FALLBACK)).toBeInTheDocument()
    expect(await headline()).toBeInTheDocument()
  })

  it('shows the 404 texts for forecast and backtest', async () => {
    mockFetch(routes({ '/api/v1/forecasts': fail(404), '/api/v1/backtest/metrics': fail(404) }))
    renderApp()
    expect(await screen.findByText(NO_FORECAST)).toBeInTheDocument()
    expect(await screen.findByText(NO_BACKTEST)).toBeInTheDocument()
  })

  it('shows the first-start message only for the "no snapshot yet" 503', async () => {
    mockFetch(routes({ '/api/v1/forecasts': fail(503, MISSING) }))
    renderApp()
    expect(await screen.findByText(NO_DATA)).toBeInTheDocument()
  })

  it.each([
    [
      'an unopenable snapshot',
      { status: 503, body: { detail: 'the data snapshot cannot be opened' } },
    ],
    ['a plain-text 503', { status: 503, raw: 'Service Unavailable' }],
  ])('reports %s as a retryable failure', async (_name, response) => {
    mockFetch(routes({ '/api/v1/backtest/metrics': () => response }))
    renderApp()
    const alert = await screen.findByRole('alert', {}, { timeout: 8000 })
    expect(alert).toHaveTextContent(
      /Could not load the backtest: the server answered with HTTP 503/,
    )
    expect(within(alert).getByRole('button', { name: 'Retry' })).toBeInTheDocument()
    expect(screen.queryByText(NO_DATA)).toBeNull()
  })

  it('keeps the day navigation when a selected day has no forecast', async () => {
    mockFetch(
      routes({
        '/api/v1/forecasts': (url) => {
          const day = url.searchParams.get('delivery_day')
          return day === '2026-11-20'
            ? { status: 404, body: { detail: 'no forecast exists for delivery day 2026-11-20' } }
            : { body: fx.forecast(day ?? '2026-11-21', false) }
        },
      }),
    )
    renderApp()
    await headline()
    fireEvent.click(screen.getByRole('button', { name: 'Previous day' }))
    expect(await screen.findByText('No forecast for Fri 20 Nov 2026.')).toBeInTheDocument()
    expect(screen.queryByText(/^Forecast peak/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Previous day' }))
    expect(await screen.findByText(/Thursday 19 November/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Next day' }))
    fireEvent.click(screen.getByRole('button', { name: 'Next day' }))
    expect(await screen.findByText(/Saturday 21 November/)).toBeInTheDocument()
  })

  it('requests the newest day once by date and notes a failing day list', async () => {
    const { calls } = mockFetch(routes())
    renderApp()
    await headline()
    const forecastCalls = calls.filter((u) => u.pathname === '/api/v1/forecasts')
    expect(forecastCalls.map((u) => u.search)).toEqual(['?delivery_day=2026-11-21'])
  })

  it('falls back to the latest forecast when the day list fails', async () => {
    let attempts = 0
    const { calls } = mockFetch(
      routes({
        '/api/v1/forecasts/days': () => {
          attempts += 1
          return attempts === 1
            ? { status: 500, body: { detail: 'boom' } }
            : { body: { days: fx.DAYS, limit: 400 } }
        },
      }),
    )
    renderApp()
    expect(
      await screen.findByText(/The list of forecast days could not be loaded/),
    ).toBeInTheDocument()
    expect(await headline()).toBeInTheDocument()
    expect(calls.find((u) => u.pathname === '/api/v1/forecasts')?.search).toBe('')
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Previous day' })).toBeEnabled()
    })
  })

  it('reports a network error and retries', async () => {
    let attempts = 0
    mockFetch(
      routes({
        '/api/v1/forecasts': () => {
          attempts += 1
          return attempts === 1
            ? Promise.reject(new TypeError('Failed to fetch'))
            : { body: fx.forecast('2026-11-21', false) }
        },
      }),
    )
    renderApp()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Could not load the forecast: the server could not be reached.')
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }))
    expect(await headline()).toBeInTheDocument()
  })

  it('treats a busy server as a failure with retry, not as missing data', async () => {
    let attempts = 0
    mockFetch(
      routes({
        '/api/v1/backtest/metrics': () => {
          attempts += 1
          return attempts === 1
            ? { status: 503, body: { detail: 'the server is busy; try again shortly' } }
            : { body: fx.backtest() }
        },
      }),
    )
    renderApp()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(
      'Could not load the backtest: the server answered with HTTP 503 (the server is busy; try again shortly).',
    )
    expect(screen.queryByText(NO_DATA)).toBeNull()
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('table', { name: /Backtest errors/ })).toBeInTheDocument()
  })

  it('shows the SMARD comparison sentence when the run scored it', async () => {
    const data = fx.backtest()
    const metric = (mape: number) => [{ scope_value: 'all', mae: 1, mape, n: 15_998 }]
    data.metrics.lightgbm = { ...data.metrics.lightgbm, overall_tso_hours: metric(2.38) }
    data.metrics.baseline_week = { ...data.metrics.baseline_week, overall_tso_hours: metric(5.81) }
    data.metrics.tso = { overall: metric(2.77) }
    mockFetch(routes({ '/api/v1/backtest/metrics': ok(data) }))
    renderApp()
    expect(
      await screen.findByText(
        'On the 15,998 hours that also have a SMARD forecast: LightGBM 2.4 %, baseline 5.8 %, SMARD forecast 2.8 %.',
      ),
    ).toBeInTheDocument()
  })

  it('reports HTTP errors of the status, series and actuals', async () => {
    mockFetch(routes({ '/api/v1/status': fail(500, 'boom') }))
    renderApp()
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Could not load the status: the server answered with HTTP 500 (boom).',
    )
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  })

  it('reports a failing series list and failing actuals', async () => {
    mockFetch(routes({ '/api/v1/series': fail(500) }))
    renderApp()
    expect(await screen.findByText(/Could not load the series list/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  })

  it('reports failing generation data and retries it', async () => {
    mockFetch(routes({ '/api/v1/actuals': fail(500), '/api/v1/backtest/metrics': fail(500) }))
    renderApp()
    expect(await screen.findByText(/Could not load the generation data/)).toBeInTheDocument()
    expect(await screen.findByText(/Could not load the backtest/)).toBeInTheDocument()
    for (const button of screen.getAllByRole('button', { name: 'Retry' })) {
      fireEvent.click(button)
    }
  })

  it('hides the price chart when prices are not served', async () => {
    const { calls } = mockFetch(routes({ '/api/v1/series': ok(fx.catalog(false)) }))
    renderApp()
    expect(await screen.findByRole('img', { name: /Stacked area chart/ })).toBeInTheDocument()
    expect(screen.queryByText(/HEDWIG/)).toBeNull()
    expect(screen.queryByRole('img', { name: /day-ahead electricity price/ })).toBeNull()
    expect(calls.find((u) => u.pathname === '/api/v1/actuals')?.searchParams.get('series')).toBe(
      'gen_wind_onshore,gen_solar',
    )
  })

  it('explains missing generation series and empty periods', async () => {
    mockFetch(routes({ '/api/v1/series': ok({ series: [] }) }))
    renderApp()
    expect(await screen.findByText(/serves no generation series/)).toBeInTheDocument()
  })

  it('explains an empty period and missing price values', async () => {
    mockFetch(
      routes({
        '/api/v1/actuals': ok({ resolution: 'hour', start: 'a', end: 'b', series: [] }),
      }),
    )
    renderApp()
    expect(
      await screen.findByText('No generation data for 14 Nov 2026 to 20 Nov 2026.'),
    ).toBeInTheDocument()
  })

  it('drops sources without data and shows no price stats without prices', async () => {
    mockFetch(
      routes({
        '/api/v1/actuals': ok({
          resolution: 'hour',
          start: 'a',
          end: 'b',
          series: [
            {
              key: 'gen_solar',
              label: 'x',
              unit: 'MWh',
              points: [{ ts: '2026-11-20T00:00:00+01:00', value: 0 }],
            },
            { key: 'gen_wind_onshore', label: 'x', unit: 'MWh', points: [] },
          ],
        }),
      }),
    )
    renderApp()
    expect(await screen.findByText('No price data in this period.')).toBeInTheDocument()
    const summary = screen.getByRole('table', { name: /Average generation/ })
    // Wind onshore has no value in the range and is left out; the solar share is undefined.
    expect(within(summary).queryByText('gen_wind_onshore')).toBeNull()
    expect(within(summary).queryByText('Wind onshore')).toBeNull()
    expect(within(summary).getAllByText('n/a')).toHaveLength(1)
  })

  it('marks stale data and pipeline runs that are still going', async () => {
    const base = fx.status()
    mockFetch(
      routes({
        '/api/v1/status': ok(
          fx.status({
            series: base.series.map((s) => ({ ...s, age_seconds: 8 * 3600 })),
            last_load_run: base.last_load_run && {
              ...base.last_load_run,
              status: 'running',
              finished_at: null,
            },
          }),
        ),
      }),
    )
    renderApp()
    expect(await screen.findByText(/more than 6 hours old/)).toBeInTheDocument()
    expect(
      screen.getByText(/Last pipeline run: running \(incremental\), started/),
    ).toBeInTheDocument()
  })

  it('omits freshness and runs that do not exist yet', async () => {
    mockFetch(
      routes({
        '/api/v1/status': ok(
          fx.status({ series: [], last_load_run: null, snapshot_created_at: null }),
        ),
      }),
    )
    renderApp()
    expect(await screen.findByText('Last pipeline run: none yet')).toBeInTheDocument()
    expect(screen.queryByText(/Data up to/)).toBeNull()
    expect(screen.queryByText(/Snapshot:/)).toBeNull()
  })
})

describe('Freshness and rollover', () => {
  it('refetches snapshot data when the snapshot changes', async () => {
    let snapshot = '2026-11-20T13:56:00+01:00'
    const { calls } = mockFetch(
      routes({ '/api/v1/status': () => ({ body: fx.status({ snapshot_created_at: snapshot }) }) }),
    )
    const { client } = renderApp()
    await headline()
    await screen.findByRole('table', { name: /Backtest errors/ })
    const count = (path: string) => calls.filter((u) => u.pathname === path).length
    const before = count('/api/v1/backtest/metrics')
    snapshot = '2026-11-20T14:56:00+01:00'
    await act(async () => {
      await client.refetchQueries({ queryKey: ['status'] })
    })
    await waitFor(() => {
      expect(count('/api/v1/backtest/metrics')).toBe(before + 1)
    })
    expect(count('/api/v1/forecasts/days')).toBe(2)
  })

  it('shows the stale warning once the data ages in an open tab', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const base = fx.status()
      mockFetch(
        routes({
          '/api/v1/status': ok(
            fx.status({ series: base.series.map((s) => ({ ...s, age_seconds: 6 * 3600 - 30 })) }),
          ),
        }),
      )
      renderApp()
      expect(await screen.findByText('Data up to 20 Nov 2026, 13:30')).toBeInTheDocument()
      expect(screen.queryByText(/more than 6 hours old/)).toBeNull()
      act(() => {
        vi.advanceTimersByTime(61_000)
      })
      expect(await screen.findByText(/more than 6 hours old/)).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('Theme toggle', () => {
  it('applies and persists the choice', async () => {
    mockFetch(routes())
    const view = renderApp()
    await headline()
    expect(screen.getByLabelText('System')).toBeChecked()
    expect(document.documentElement.dataset.theme).toBe('light')

    fireEvent.click(screen.getByLabelText('Dark'))
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(window.localStorage.getItem('gridload-theme')).toBe('dark')

    view.unmount()
    renderApp()
    expect(screen.getByLabelText('Dark')).toBeChecked()
    fireEvent.click(screen.getByLabelText('System'))
    expect(window.localStorage.getItem('gridload-theme')).toBeNull()
  })

  it('follows the system preference', async () => {
    let listener: () => void = () => undefined
    let dark = false
    vi.stubGlobal('matchMedia', () => ({
      get matches() {
        return dark
      },
      addEventListener: (_: string, fn: () => void) => (listener = fn),
      removeEventListener: () => undefined,
    }))
    mockFetch(routes())
    renderApp()
    await headline()
    expect(document.documentElement.dataset.theme).toBe('light')
    dark = true
    act(() => {
      listener()
    })
    await waitFor(() => {
      expect(document.documentElement.dataset.theme).toBe('dark')
    })
  })

  it('still switches when storage is blocked', async () => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('blocked')
      },
      setItem: () => {
        throw new Error('blocked')
      },
      removeItem: () => {
        throw new Error('blocked')
      },
      clear: () => undefined,
    })
    mockFetch(routes())
    renderApp()
    await headline()
    fireEvent.click(screen.getByLabelText('Light'))
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(screen.getByLabelText('Light')).toBeChecked()
  })
})
