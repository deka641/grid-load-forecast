import { act, render } from '@testing-library/react'

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
})
