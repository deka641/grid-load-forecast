import { isApiError } from '../api/client'

export const NO_DATA =
  'No data yet. The pipeline loads the history on its first start; this takes about 75 minutes.'
export const NO_FORECAST =
  'No forecast yet. The first forecast is issued at 12:00 (Europe/Berlin) once the history is loaded.'
export const NO_BACKTEST = 'No backtest yet. It runs after the first forecast.'

export function describeError(error: unknown): string {
  if (isApiError(error)) {
    return `the server answered with HTTP ${String(error.status)} (${error.message})`
  }
  return 'the server could not be reached'
}
