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
let height = 0
const loaded = { status: 'loaded', ready: Promise.resolve() }

describe('Chart resize', () => {
  beforeAll(() => {
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
      configurable: true,
      get: () => width,
    })
    Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
      configurable: true,
      get: () => height,
    })
  })

  afterAll(() => {
    Reflect.deleteProperty(HTMLElement.prototype, 'clientWidth')
    Reflect.deleteProperty(HTMLElement.prototype, 'clientHeight')
    Reflect.deleteProperty(document, 'fonts')
  })

  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('ResizeObserver', ObserverMock)
    vi.stubGlobal(
      'matchMedia',
      vi.fn((query: string) => ({ matches: query.includes('no-preference') })),
    )
    Object.defineProperty(document, 'fonts', { value: loaded, configurable: true })
  })

  it('ignores the report of the size the chart already has, which would end the draw-in', () => {
    width = 900
    height = 380
    const build = vi.fn(() => ({}))
    render(<Chart build={build} label="Same size" />)
    expect(build).toHaveBeenCalledTimes(1)

    // The observer's first report after observe() carries the size from init.
    act(() => resizeCallback?.())
    expect(instance.resize).not.toHaveBeenCalled()

    height = 300
    act(() => resizeCallback?.())
    expect(instance.resize).toHaveBeenCalledTimes(1)

    // The same new size again is no change either.
    act(() => resizeCallback?.())
    expect(instance.resize).toHaveBeenCalledTimes(1)

    width = 880
    act(() => resizeCallback?.())
    expect(instance.resize).toHaveBeenCalledTimes(2)
  })

  it('keeps the draw-in on a narrow chart when the width class is set while the font loads', async () => {
    width = 358
    height = 300
    let ready: () => void = () => undefined
    const loading = { status: 'loading', ready: new Promise<void>((done) => (ready = done)) }
    Object.defineProperty(document, 'fonts', { value: loading, configurable: true })
    const build = vi.fn(() => ({}))
    render(<Chart build={build} label="Narrow" />)
    expect(build).not.toHaveBeenCalled()
    await act(async () => {
      ready()
      await loading.ready
    })
    expect(build).toHaveBeenCalledTimes(1)
    expect(build).toHaveBeenLastCalledWith(expect.anything(), {
      compact: true,
      motion: true,
      animate: true,
    })
  })
})
