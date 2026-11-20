import { QueryClient } from '@tanstack/react-query'
import { render } from '@testing-library/react'

import { App } from '../App'

/** `raw` sends a plain-text body instead of JSON, like a proxy or server limit would. */
export type Handler = (
  url: URL,
) => { status?: number; body?: unknown; raw?: string } | Promise<never>

/** Replace fetch with a router over pathname. Unknown paths answer 404. */
export function mockFetch(routes: Record<string, Handler>) {
  const calls: URL[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const href = input instanceof Request ? input.url : input instanceof URL ? input.href : input
    const url = new URL(href, 'http://localhost')
    calls.push(url)
    const handler = routes[url.pathname]
    if (!handler) {
      return new Response(JSON.stringify({ detail: 'not found' }), { status: 404 })
    }
    const { status = 200, body, raw } = await handler(url)
    if (raw !== undefined) {
      return new Response(raw, { status, statusText: raw })
    }
    return new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, calls }
}

export function testClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  })
}

export function renderApp() {
  const client = testClient()
  return { client, ...render(<App client={client} />) }
}
