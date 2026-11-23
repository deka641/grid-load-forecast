import { screen } from '@testing-library/react'
import type { ReactNode } from 'react'

import type { ForecastDay } from '../api/client'
import { mockFetch, renderApp } from '../test/api'
import * as fx from '../test/fixtures'

vi.mock('../charts/Chart', () => ({
  Chart: ({ label, children }: { label: string; children?: ReactNode }) => (
    <figure role="img" aria-label={label}>
      {children}
    </figure>
  ),
}))

type Run = ForecastDay['runs'][string]

function run(extra: Partial<Run>): Run {
  return {
    run_id: 'fc',
    issued_at: '2026-11-20T12:00:00+01:00',
    created_at: '2026-11-20T12:05:00+01:00',
    ...extra,
  }
}

function serve(day: ForecastDay, days: string[] = fx.DAYS) {
  mockFetch({
    '/api/v1/status': () => ({ body: fx.status() }),
    '/api/v1/series': () => ({ body: fx.catalog() }),
    '/api/v1/forecasts/days': () => ({ body: { days, limit: 400 } }),
    '/api/v1/forecasts': () => ({ body: day }),
    '/api/v1/backtest/metrics': () => ({ body: fx.backtest() }),
    '/api/v1/actuals': (url) => ({
      body: fx.actuals((url.searchParams.get('series') ?? '').split(',')),
    }),
  })
}

const NOTICE = /Computed with incomplete load data/

describe('Degraded forecast notice', () => {
  it('names the missing inputs of a degraded model run', async () => {
    const day = fx.forecast('2026-11-21', false)
    day.runs = {
      lightgbm: run({ degraded: true, degraded_checks: ['lag_7d', 'cutoff_hour_missing'] }),
      baseline_week: run({ degraded: null, degraded_checks: null }),
    }
    serve(day)
    renderApp()
    const title = await screen.findByText(NOTICE)
    const notice = title.closest('.notice')
    expect(notice).not.toBeNull()
    const items = [...(notice?.querySelectorAll('li') ?? [])].map((li) => li.textContent)
    expect(items).toEqual([
      'the load of the last hour before the cutoff (09:00 to 10:00 on the day before)',
      'for some hours, the load of the same hour seven days earlier',
    ])
    expect(notice).toHaveTextContent('may be less accurate than the backtest suggests')
    expect(notice).not.toHaveAttribute('role', 'alert')
  })

  it('still explains a degraded run without known checks', async () => {
    const day = fx.forecast('2026-11-21', false)
    day.runs = { lightgbm: run({ degraded: true, degraded_checks: null }) }
    serve(day)
    renderApp()
    const title = await screen.findByText(NOTICE)
    const notice = title.closest('.notice')
    expect(notice).toHaveTextContent('Some of its inputs were missing')
    expect(notice?.querySelector('ul')).toBeNull()

    const other = fx.forecast('2026-11-21', false)
    other.runs = { lightgbm: run({ degraded: true, degraded_checks: ['new_check'] }) }
    serve(other)
    renderApp()
    expect(await screen.findByText('some of its input data')).toBeInTheDocument()
  })

  it.each([
    ['false', { degraded: false, degraded_checks: [] }],
    ['null', { degraded: null, degraded_checks: null }],
    ['missing', {}],
  ])('shows nothing when degraded is %s', async (_, extra) => {
    const day = fx.forecast('2026-11-21', false)
    day.runs = { lightgbm: run(extra), baseline_week: run({ degraded: true }) }
    serve(day)
    renderApp()
    await screen.findByText(/^Forecast peak for/)
    expect(screen.queryByText(NOTICE)).toBeNull()
  })
})

describe('Day selector', () => {
  it.each([[['2026-11-21']], [[]]])(
    'shows a single day as text without dead buttons (list %j)',
    async (days) => {
      serve(fx.forecast('2026-11-21', false), days)
      renderApp()
      await screen.findByText(/^Forecast peak for/)
      expect(screen.getByText('Delivery day').parentElement).toHaveTextContent(
        'Delivery day Sat 21 Nov 2026',
      )
      expect(screen.queryByRole('button', { name: 'Previous day' })).toBeNull()
      expect(screen.queryByRole('button', { name: 'Next day' })).toBeNull()
      expect(screen.queryByLabelText('Delivery day')).toBeNull()
    },
  )

  it('keeps the buttons and the list with two days', async () => {
    serve(fx.forecast('2026-11-21', false), ['2026-11-21', '2026-11-20'])
    renderApp()
    await screen.findByText(/^Forecast peak for/)
    expect(screen.getByRole('button', { name: 'Next day' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Previous day' })).toBeEnabled()
    expect(screen.getByLabelText('Delivery day')).toHaveValue('2026-11-21')
  })

  it('says nothing about skipped days when the previous day is the day before', async () => {
    serve(fx.forecast('2026-11-21', false), ['2026-11-21', '2026-11-20'])
    renderApp()
    await screen.findByText(/^Forecast peak for/)
    expect(screen.queryByText(/^No forecast was issued/)).toBeNull()
    expect(screen.getByRole('button', { name: 'Previous day' })).not.toHaveAttribute(
      'aria-describedby',
    )
  })

  it.each([
    [['2026-11-24', '2026-11-22', '2026-11-21'], 'No forecast was issued for Mon 23 Nov 2026.'],
    [
      ['2026-11-24', '2026-11-21'],
      'No forecast was issued for Sun 22 Nov 2026 and Mon 23 Nov 2026.',
    ],
    [
      ['2026-11-24', '2026-11-19'],
      'No forecast was issued for the 4 days from Fri 20 Nov 2026 to Mon 23 Nov 2026.',
    ],
  ])('names the days without a forecast before the shown day (%j)', async (days, text) => {
    serve(fx.forecast('2026-11-24', false), days)
    renderApp()
    await screen.findByText(/^Forecast peak for/)
    expect(screen.getByText(/^No forecast was issued/)).toHaveTextContent(text)
    // The note describes the button that skips the gap, and the controls stay as they were.
    const previous = screen.getByRole('button', { name: 'Previous day' })
    expect(previous).toHaveAccessibleDescription(text)
    expect(previous).toBeEnabled()
    expect(screen.getByLabelText('Delivery day')).toHaveValue('2026-11-24')
  })

  it('counts calendar days across a clock change', async () => {
    serve(fx.forecast('2026-10-27', false), ['2026-10-27', '2026-10-24'])
    renderApp()
    await screen.findByText(/^Forecast peak for/)
    expect(screen.getByText(/^No forecast was issued/)).toHaveTextContent(
      'No forecast was issued for Sun 25 Oct 2026 and Mon 26 Oct 2026.',
    )
  })
})
