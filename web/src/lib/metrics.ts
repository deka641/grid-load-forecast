import type { BacktestMetric, BacktestMetrics, ForecastPoint } from '../api/client'
import { dayLong, wallClock } from './time'
import { formatGW } from './format'

export type ForecastKey = 'lightgbm' | 'baseline_week' | 'tso'

export const MODELS: readonly { key: ForecastKey; label: string; short: string }[] = [
  { key: 'lightgbm', label: 'LightGBM model', short: 'LightGBM' },
  { key: 'baseline_week', label: 'Baseline (same hour last week)', short: 'Baseline' },
  { key: 'tso', label: 'SMARD forecast', short: 'SMARD forecast' },
]

export interface DayError {
  mape: number
  hours: number
}

/** MAPE in percent of one forecast against the actual, over hours where both exist. */
export function dayMape(points: readonly ForecastPoint[], key: ForecastKey): DayError | null {
  let sum = 0
  let hours = 0
  for (const point of points) {
    const forecast = point[key]
    const actual = point.actual
    if (forecast === null || actual === null || actual === 0) {
      continue
    }
    sum += Math.abs(forecast - actual) / Math.abs(actual)
    hours += 1
  }
  return hours === 0 ? null : { mape: (sum / hours) * 100, hours }
}

export function hasActuals(points: readonly ForecastPoint[]): boolean {
  return points.some((p) => p.actual !== null)
}

export interface Peak {
  value: number
  ts: string
}

/** The highest LightGBM value of the day; the first one wins on ties. */
export function forecastPeak(points: readonly ForecastPoint[]): Peak | null {
  let peak: Peak | null = null
  for (const point of points) {
    if (point.lightgbm !== null && (peak === null || point.lightgbm > peak.value)) {
      peak = { value: point.lightgbm, ts: point.ts }
    }
  }
  return peak
}

export function headline(deliveryDay: string, points: readonly ForecastPoint[]): string {
  const peak = forecastPeak(points)
  const day = dayLong(deliveryDay)
  if (peak === null) {
    return `No model forecast for ${day}`
  }
  return `Forecast peak for ${day}: ${formatGW(peak.value)} at ${wallClock(peak.ts)}`
}

export function findMetric(
  backtest: BacktestMetrics,
  model: ForecastKey,
  scope: string,
  scopeValue: string,
): BacktestMetric | undefined {
  return backtest.metrics[model]?.[scope]?.find((m) => m.scope_value === scopeValue)
}

/** MAPE by local hour 0 to 23; missing hours are null. */
export function mapeByHour(backtest: BacktestMetrics, model: ForecastKey): (number | null)[] {
  const byHour = new Map<number, number>()
  for (const metric of backtest.metrics[model]?.hour ?? []) {
    byHour.set(Number(metric.scope_value), metric.mape)
  }
  return Array.from({ length: 24 }, (_, hour) => byHour.get(hour) ?? null)
}
