import { act, fireEvent, render, screen } from '@testing-library/react'

import { ThemeContext } from '../theme/context'
import type { ThemeState } from '../theme/context'
import { Chart } from './Chart'

const instance = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() }
const init = vi.fn(() => instance)

vi.mock('./echarts', () => ({ echarts: { init: (...args: unknown[]) => init(...(args as [])) } }))

let resizeCallback: (() => void) | undefined
const disconnect = vi.fn()

class ObserverMock {
  constructor(callback: () => void) {
    resizeCallback = callback
  }
  observe() {
    return undefined
  }
  disconnect() {
    disconnect()
  }
}

function theme(resolved: 'light' | 'dark'): ThemeState {
  return { preference: resolved, resolved, setPreference: () => undefined }
}

describe('Chart', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('ResizeObserver', ObserverMock)
  })

  it('initialises, renders, resizes, re-renders on theme change and disposes', async () => {
    const build = vi.fn(() => ({ series: [] }))
    const view = render(
      <ThemeContext value={theme('light')}>
        <Chart build={build} label="Test chart" className="extra">
          <table />
        </Chart>
      </ThemeContext>,
    )
    expect(screen.getByRole('img', { name: 'Test chart' })).toBeInTheDocument()
    expect(document.querySelector('figure.chart.extra figcaption')).not.toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Show data' }))
    expect(screen.getByRole('region', { name: 'Chart data' })).toBeInTheDocument()
    expect(init).toHaveBeenCalledWith(expect.any(HTMLDivElement), null, { renderer: 'canvas' })
    expect(instance.setOption).toHaveBeenCalledWith({ series: [] }, { notMerge: true })

    // A resize only counts once the size differs from the one at init.
    Object.defineProperty(screen.getByRole('img', { name: 'Test chart' }), 'clientWidth', {
      configurable: true,
      value: 640,
    })
    act(() => {
      resizeCallback?.()
    })
    expect(instance.resize).toHaveBeenCalled()

    const calls = build.mock.calls.length
    view.rerender(
      <ThemeContext value={theme('dark')}>
        <Chart build={build} label="Test chart" className="extra" />
      </ThemeContext>,
    )
    expect(build.mock.calls.length).toBeGreaterThan(calls)
    expect(document.querySelector('figcaption')).toBeNull()

    view.unmount()
    expect(disconnect).toHaveBeenCalled()
    expect(instance.dispose).toHaveBeenCalled()
    await Promise.resolve()
  })

  it('toggles the data table visually', () => {
    render(
      <Chart build={() => ({})} label="Toggle" dataLabel="Values table">
        <table aria-label="values" />
      </Chart>,
    )
    const button = screen.getByRole('button', { name: 'Show data' })
    expect(button).toHaveAttribute('aria-expanded', 'false')
    const caption = document.getElementById(button.getAttribute('aria-controls') ?? '')
    expect(caption).toHaveClass('visually-hidden')
    // Closed: no tab stop and no landmark on the hidden copy.
    expect(screen.queryByRole('region')).toBeNull()
    expect(caption?.querySelector('[tabindex]')).toBeNull()
    fireEvent.click(button)
    expect(screen.getByRole('button', { name: 'Hide data' })).toHaveAttribute(
      'aria-expanded',
      'true',
    )
    expect(caption).toHaveClass('chart-data')
    // Open: the scroll box is a named region that the keyboard can reach.
    const region = screen.getByRole('region', { name: 'Values table' })
    expect(region).toHaveAttribute('tabindex', '0')
    expect(region).toHaveClass('chart-data-scroll')
    expect(caption).toContainElement(region)
    fireEvent.click(screen.getByRole('button', { name: 'Hide data' }))
    expect(screen.queryByRole('region')).toBeNull()
  })

  it('renders once when the fonts are already loaded', () => {
    const fonts = { status: 'loaded', ready: Promise.resolve() }
    Object.defineProperty(document, 'fonts', { value: fonts, configurable: true })
    render(<Chart build={() => ({})} label="Loaded" />)
    expect(instance.setOption).toHaveBeenCalledTimes(1)
    Reflect.deleteProperty(document, 'fonts')
  })

  it('renders again once the web fonts are ready', async () => {
    let ready: () => void = () => undefined
    const fonts = { status: 'loading', ready: new Promise<void>((resolve) => (ready = resolve)) }
    Object.defineProperty(document, 'fonts', { value: fonts, configurable: true })
    const build = vi.fn(() => ({}))
    const view = render(<Chart build={build} label="Fonts" />)
    expect(document.querySelector('figure')?.className).toBe('chart')
    const before = instance.setOption.mock.calls.length
    await act(async () => {
      ready()
      await fonts.ready
    })
    expect(instance.setOption.mock.calls.length).toBe(before + 1)

    view.unmount()
    // A late font promise after unmount does not touch the disposed chart.
    Reflect.deleteProperty(document, 'fonts')
  })
})
