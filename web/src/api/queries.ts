import { QueryClient, keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiGet, isApiError, isNoData } from './client'

/** 404 and 503 describe the data, not a transient failure, so they are not retried. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (isApiError(error) && error.status < 500) {
    return false
  }
  if (isNoData(error)) {
    return false
  }
  return failureCount < 2
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: shouldRetry,
        staleTime: 5 * 60 * 1000,
        refetchOnWindowFocus: false,
      },
    },
  })
}

export const STATUS_REFRESH_MS = 5 * 60 * 1000

/** Polled, so freshness, the stale warning and a new snapshot show up in an open tab. */
export function useStatus() {
  return useQuery({
    queryKey: ['status'],
    queryFn: ({ signal }) => apiGet('/api/v1/status', undefined, signal),
    refetchInterval: STATUS_REFRESH_MS,
    refetchOnWindowFocus: true,
    staleTime: 0,
  })
}

/** Query keys that hold snapshot data and must be refetched for a new snapshot. */
export const SNAPSHOT_QUERIES = [
  ['forecast'],
  ['forecast-days'],
  ['actuals'],
  ['backtest'],
  ['series'],
]

export function useSeries(enabled: boolean) {
  return useQuery({
    queryKey: ['series'],
    queryFn: ({ signal }) => apiGet('/api/v1/series', undefined, signal),
    enabled,
  })
}

export function useForecastDays(enabled: boolean) {
  return useQuery({
    queryKey: ['forecast-days'],
    queryFn: ({ signal }) => apiGet('/api/v1/forecasts/days', undefined, signal),
    enabled,
  })
}

/** `day` undefined asks the API for the latest delivery day with a forecast. */
export function useForecast(day: string | undefined, enabled: boolean) {
  const client = useQueryClient()
  return useQuery({
    queryKey: ['forecast', day ?? 'latest'],
    queryFn: async ({ signal }) => {
      const result = await apiGet(
        '/api/v1/forecasts',
        day === undefined ? {} : { delivery_day: day },
        signal,
      )
      if (day === undefined) {
        // Seed the dated key, so selecting this day later needs no second request.
        client.setQueryData(['forecast', result.delivery_day], result)
      }
      return result
    },
    enabled,
    // Keep the previous day on screen while the next one loads.
    placeholderData: keepPreviousData,
  })
}

export function useBacktest(enabled: boolean) {
  return useQuery({
    queryKey: ['backtest'],
    queryFn: ({ signal }) => apiGet('/api/v1/backtest/metrics', undefined, signal),
    enabled,
  })
}

export function useActuals(keys: string[], start: string, end: string, enabled: boolean) {
  return useQuery({
    queryKey: ['actuals', keys.join(','), start, end],
    queryFn: ({ signal }) =>
      apiGet('/api/v1/actuals', { series: keys.join(','), start, end, resolution: 'hour' }, signal),
    enabled: enabled && keys.length > 0,
  })
}
