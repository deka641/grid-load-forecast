import { act, render } from '@testing-library/react'
import { StrictMode } from 'react'

import { Chart } from './Chart'

const instance = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() }

vi.mock('./echarts', () => ({ echarts: { init: () => instance } }))

let resizeCallback: (() => void) | undefined

class ObserverMock {
  constructor(callback: () => void) {
    resizeCallback = callback
  }
  observe() {
    return undefined
  }
  disconnect() {
    return undefined
  }
}

let width = 0
const fonts = { status: 'loaded', ready: Promise.resolve() }

function motion(allowed: boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({ matches: allowed && query.includes('no-preference') })),
  )
}

describe('Chart layout', () => {
  beforeAll(() => {
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
      configurable: true,
      get: () => width,
    })
    Object.defineProperty(document, 'fonts', { value: fonts, configurable: true })
  })

  afterAll(() => {
    Reflect.deleteProperty(HTMLElement.prototype, 'clientWidth')
    Reflect.deleteProperty(document, 'fonts')
  })

  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('ResizeObserver', ObserverMock)
  })

  it('draws in on the first render only and renders again when the width class changes', () => {
    width = 358
    motion(true)
    const build = vi.fn(() => ({}))
    render(<Chart build={build} label="Layout" />)
    expect(build).toHaveBeenCalledTimes(1)
    expect(build).toHaveBeenLastCalledWith(expect.anything(), {
      compact: true,
      motion: true,
      animate: true,
    })

    // A resize within the same width class keeps the option and does not replay the draw-in.
    width = 400
    act(() => resizeCallback?.())
    expect(build).toHaveBeenCalledTimes(1)
    expect(instance.resize).toHaveBeenCalled()

    width = 900
    act(() => resizeCallback?.())
    expect(build).toHaveBeenCalledTimes(2)
    expect(build).toHaveBeenLastCalledWith(expect.anything(), {
      compact: false,
      motion: true,
      animate: false,
    })
  })

  it('waits for the web font before the draw-in, so no second render cuts it short', async () => {
    width = 900
    motion(true)
    let ready: () => void = () => undefined
    const loading = { status: 'loading', ready: new Promise<void>((done) => (ready = done)) }
    Object.defineProperty(document, 'fonts', { value: loading, configurable: true })
    try {
      const build = vi.fn(() => ({}))
      render(<Chart build={build} label="Font first" />)
      expect(build).not.toHaveBeenCalled()
      await act(async () => {
        ready()
        await loading.ready
      })
      expect(build).toHaveBeenCalledTimes(1)
      expect(build).toHaveBeenLastCalledWith(expect.anything(), {
        compact: false,
        motion: true,
        animate: true,
      })
    } finally {
      Object.defineProperty(document, 'fonts', { value: fonts, configurable: true })
    }
  })

  it('draws the new instance after a StrictMode remount', () => {
    width = 900
    motion(false)
    render(
      <StrictMode>
        <Chart build={() => ({})} label="Strict" />
      </StrictMode>,
    )
    // The first instance is disposed by the remount; the second one must get an option.
    expect(instance.dispose).toHaveBeenCalledTimes(1)
    const disposed = instance.dispose.mock.invocationCallOrder[0] ?? 0
    expect(instance.setOption.mock.invocationCallOrder.some((order) => order > disposed)).toBe(true)
  })

  it('never animates when reduced motion is requested or cannot be queried', () => {
    width = 900
    motion(false)
    const build = vi.fn(() => ({}))
    const view = render(<Chart build={build} label="Still" />)
    expect(build).toHaveBeenLastCalledWith(expect.anything(), {
      compact: false,
      motion: false,
      animate: false,
    })
    view.unmount()

    vi.stubGlobal('matchMedia', undefined)
    render(<Chart build={build} label="No media queries" />)
    expect(build).toHaveBeenLastCalledWith(expect.anything(), {
      compact: false,
      motion: false,
      animate: false,
    })
  })
  it('plays the accent once after the draw-in and never without motion', () => {
    vi.useFakeTimers()
    width = 900
    const series = { id: 'ring', type: 'effectScatter', data: [[1, 2]] }
    const arrival = vi.fn(() => ({ series, delay: 1000, duration: 500 }))
    const build = vi.fn(() => ({}))
    motion(true)
    const view = render(<Chart build={build} arrival={arrival} label="Accent" />)
    expect(instance.setOption).toHaveBeenCalledTimes(1)
    act(() => vi.advanceTimersByTime(999))
    expect(instance.setOption).toHaveBeenCalledTimes(1)
    act(() => vi.advanceTimersByTime(1))
    expect(instance.setOption).toHaveBeenLastCalledWith({ series: [series] })
    act(() => vi.advanceTimersByTime(500))
    expect(instance.setOption).toHaveBeenLastCalledWith({ series: [{ id: 'ring', data: [] }] })
    view.unmount()

    vi.clearAllMocks()
    motion(false)
    render(<Chart build={build} arrival={arrival} label="Still accent" />)
    act(() => vi.advanceTimersByTime(5000))
    expect(arrival).not.toHaveBeenCalled()
    expect(instance.setOption).toHaveBeenCalledTimes(1)
    vi.useRealTimers()
  })
})
