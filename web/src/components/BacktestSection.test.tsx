import { act, screen } from '@testing-library/react'
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

/** A ResizeObserver whose callbacks the test fires by hand. */
const observers: { callback: () => void; targets: Element[] }[] = []
class ManualResizeObserver {
  private readonly entry: { callback: () => void; targets: Element[] }
  constructor(callback: () => void) {
    this.entry = { callback, targets: [] }
    observers.push(this.entry)
  }
  observe(target: Element) {
    this.entry.targets.push(target)
  }
  unobserve() {
    return undefined
  }
  disconnect() {
    this.entry.targets = []
  }
}

function setWidths(element: Element, scroll: number, client: number) {
  Object.defineProperty(element, 'scrollWidth', { configurable: true, value: scroll })
  Object.defineProperty(element, 'clientWidth', { configurable: true, value: client })
}

function serve(backtestDelay?: Promise<never>) {
  mockFetch({
    '/api/v1/status': () => ({ body: fx.status() }),
    '/api/v1/series': () => ({ body: fx.catalog() }),
    '/api/v1/forecasts/days': () => ({ body: { days: fx.DAYS, limit: 400 } }),
    '/api/v1/forecasts': () => ({ body: fx.forecast('2026-11-21', false) }),
    '/api/v1/backtest/metrics': () => backtestDelay ?? { body: fx.backtest() },
    '/api/v1/actuals': (url) => ({
      body: fx.actuals((url.searchParams.get('series') ?? '').split(',')),
    }),
  })
}

describe('Backtest table box', () => {
  beforeEach(() => {
    observers.length = 0
    vi.stubGlobal('ResizeObserver', ManualResizeObserver)
  })

  it('is a keyboard stop only while the table scrolls sideways', async () => {
    serve()
    renderApp()
    const table = await screen.findByRole('table', { name: /Backtest errors/ })
    const box = table.parentElement
    if (!box) {
      throw new Error('table without box')
    }
    // The table fits: no tab stop, no region.
    expect(box).not.toHaveAttribute('tabindex')
    expect(screen.queryByRole('region', { name: 'Backtest errors table' })).toBeNull()
    const watcher = observers.find((o) => o.targets.includes(box))
    expect(watcher?.targets).toContain(table)

    setWidths(box, 700, 500)
    act(() => {
      watcher?.callback()
    })
    expect(screen.getByRole('region', { name: 'Backtest errors table' })).toHaveAttribute(
      'tabindex',
      '0',
    )

    setWidths(box, 500, 500)
    act(() => {
      watcher?.callback()
    })
    expect(box).not.toHaveAttribute('tabindex')
    expect(box).not.toHaveAttribute('role')
  })

  it('reserves space for the section while it loads', async () => {
    serve(new Promise<never>(() => undefined))
    renderApp()
    const loading = await screen.findByText('Loading backtest')
    expect(loading.parentElement).toHaveClass('loading-backtest')
  })
})

describe('Backtest caveats', () => {
  it('names the bias in either direction', async () => {
    serve()
    renderApp()
    const note = await screen.findByText(/^The SMARD forecast is scored on its values/)
    expect(note).toHaveTextContent('may include revisions made after 12:00 on the day before.')
    expect(note).toHaveTextContent(
      'The model is scored with the load values as stored today, including later corrections ' +
        'it would not have had at the cutoff, which can favour the model.',
    )
  })
})
