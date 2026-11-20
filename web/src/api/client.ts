import type { components, paths } from './schema'

export type Schemas = components['schemas']
export type Status = Schemas['Status']
export type SeriesCatalog = Schemas['SeriesCatalog']
export type Actuals = Schemas['Actuals']
export type ForecastDay = Schemas['ForecastDay']
export type ForecastPoint = Schemas['ForecastPoint']
export type ForecastDays = Schemas['ForecastDays']
export type BacktestMetrics = Schemas['BacktestMetrics']
export type BacktestMetric = Schemas['BacktestMetric']

type GetOperation<P extends keyof paths> = paths[P]['get']

type QueryOf<P extends keyof paths> =
  GetOperation<P> extends { parameters: { query: infer Q } }
    ? Q
    : GetOperation<P> extends { parameters: { query?: infer Q } }
      ? [Q] extends [never]
        ? undefined
        : Q | undefined
      : undefined

type ResponseOf<P extends keyof paths> =
  GetOperation<P> extends { responses: { 200: { content: { 'application/json': infer R } } } }
    ? R
    : never

/** An HTTP response outside 2xx. Network failures stay plain errors. */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export function isApiError(error: unknown, status?: number): error is ApiError {
  return error instanceof ApiError && (status === undefined || error.status === status)
}

export function buildUrl(
  path: string,
  query?: Record<string, string | number | undefined>,
): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined) {
      params.set(key, String(value))
    }
  }
  const search = params.toString()
  return search ? `${path}?${search}` : path
}

async function detailOf(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json()
    if (typeof body === 'object' && body !== null && 'detail' in body) {
      const { detail } = body
      if (typeof detail === 'string') {
        return detail
      }
    }
  } catch {
    // The body is not JSON; fall back to the status text.
  }
  return response.statusText || `HTTP ${String(response.status)}`
}

/** GET a typed JSON endpoint of the API. */
export async function apiGet<P extends keyof paths>(
  path: P,
  query?: QueryOf<P>,
  signal?: AbortSignal,
): Promise<ResponseOf<P>> {
  const url = buildUrl(path, query as Record<string, string | number | undefined> | undefined)
  const response = await fetch(url, {
    headers: { Accept: 'application/json' },
    ...(signal ? { signal } : {}),
  })
  if (!response.ok) {
    throw new ApiError(response.status, await detailOf(response))
  }
  return (await response.json()) as ResponseOf<P>
}
