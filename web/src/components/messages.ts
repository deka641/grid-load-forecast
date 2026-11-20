import { isApiError } from '../api/client'
import { dayShort } from '../lib/time'

export const NO_DATA =
  'No data yet. The pipeline loads the full history on its first start; check back later.'
export const SNAPSHOT_FALLBACK =
  'A newer data snapshot could not be read; showing the previous one.'
export const NO_FORECAST =
  'No forecast yet. The first forecast is issued at 12:00 (Europe/Berlin) once the history is loaded.'
export const NO_BACKTEST = 'No backtest yet. It runs once enough history is loaded.'

export function noForecastFor(day: string): string {
  return `No forecast for ${dayShort(day)}.`
}

export function describeError(error: unknown): string {
  if (isApiError(error)) {
    return `the server answered with HTTP ${String(error.status)} (${error.message})`
  }
  return 'the server could not be reached'
}
