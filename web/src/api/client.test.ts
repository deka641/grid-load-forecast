import { ApiError, apiGet, buildUrl, isApiError, isNoData, isTransient } from './client'
import { createQueryClient, shouldRetry } from './queries'

describe('buildUrl', () => {
  it('adds defined query parameters only', () => {
    expect(buildUrl('/api/v1/status')).toBe('/api/v1/status')
    expect(buildUrl('/a', { x: 'b,c', y: undefined, z: 3 })).toBe('/a?x=b%2Cc&z=3')
  })
})

describe('apiGet', () => {
  it('returns the parsed body', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(Response.json({ days: [], limit: 400 })))
    vi.stubGlobal('fetch', fetchMock)
    await expect(apiGet('/api/v1/forecasts/days')).resolves.toEqual({ days: [], limit: 400 })
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/forecasts/days', {
      headers: { Accept: 'application/json' },
    })
  })

  it('passes the query and the abort signal', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(Response.json({})))
    vi.stubGlobal('fetch', fetchMock)
    const signal = new AbortController().signal
    await apiGet('/api/v1/forecasts', { delivery_day: '2026-11-21' }, signal)
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/forecasts?delivery_day=2026-11-21', {
      headers: { Accept: 'application/json' },
      signal,
    })
  })

  it('throws ApiError with the detail of the body', async () => {
    vi.stubGlobal('fetch', () =>
      Promise.resolve(Response.json({ detail: 'no forecast' }, { status: 404 })),
    )
    const error: unknown = await apiGet('/api/v1/forecasts').catch((e: unknown) => e)
    expect(isApiError(error, 404)).toBe(true)
    expect((error as ApiError).message).toBe('no forecast')
  })

  it('falls back to the status text for other bodies', async () => {
    vi.stubGlobal('fetch', () =>
      Promise.resolve(new Response('oops', { status: 502, statusText: 'Bad Gateway' })),
    )
    await expect(apiGet('/api/v1/status')).rejects.toThrow('Bad Gateway')
    vi.stubGlobal('fetch', () => Promise.resolve(Response.json({ detail: 3 }, { status: 500 })))
    await expect(apiGet('/api/v1/status')).rejects.toThrow('HTTP 500')
  })
})

describe('retry policy', () => {
  it('does not retry data states but retries transient failures twice', () => {
    expect(shouldRetry(0, new ApiError(404, 'x'))).toBe(false)
    expect(shouldRetry(0, new ApiError(503, 'x'))).toBe(false)
    expect(shouldRetry(0, new ApiError(503, 'the server is busy; try again shortly'))).toBe(true)
    expect(isTransient(new ApiError(503, 'the data snapshot cannot be read'))).toBe(true)
    expect(isNoData(new ApiError(503, 'no data snapshot exists yet'))).toBe(true)
    expect(isNoData(new ApiError(404, 'x'))).toBe(false)
    expect(shouldRetry(0, new ApiError(500, 'x'))).toBe(true)
    expect(shouldRetry(1, new TypeError('network'))).toBe(true)
    expect(shouldRetry(2, new TypeError('network'))).toBe(false)
    expect(isApiError(new Error('x'))).toBe(false)
  })

  it('configures the client with it', () => {
    expect(createQueryClient().getDefaultOptions().queries?.retry).toBe(shouldRetry)
  })
})
