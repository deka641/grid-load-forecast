import type { BacktestMetric, BacktestMetrics, ForecastPoint } from '../api/client'
import { formatGW, formatPercent } from './format'
import { dayLong, dayPlain, hourLabels } from './time'

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

export type DayErrors = Record<ForecastKey, DayError | null>

function score(points: readonly ForecastPoint[], key: ForecastKey): DayError | null {
  let sum = 0
  for (const point of points) {
    const actual = point.actual ?? 0
    sum += Math.abs((point[key] ?? 0) - actual) / Math.abs(actual)
  }
  return points.length === 0 ? null : { mape: (sum / points.length) * 100, hours: points.length }
}

/**
 * MAPE in percent for one delivery day, scored like the backtest (metrics_for in
 * src/gridload/forecast/backtest.py): LightGBM and the baseline on hours with an actual
 * and both predictions, the SMARD forecast on the subset of those with a SMARD value.
 * Hours with a zero actual are skipped because MAPE is undefined for them.
 */
export function dayErrors(points: readonly ForecastPoint[]): DayErrors {
  const primary = points.filter(
    (p) => p.actual !== null && p.actual !== 0 && p.lightgbm !== null && p.baseline_week !== null,
  )
  return {
    lightgbm: score(primary, 'lightgbm'),
    baseline_week: score(primary, 'baseline_week'),
    tso: score(
      primary.filter((p) => p.tso !== null),
      'tso',
    ),
  }
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

export interface HeadlineParts {
  /** The sentence up to the colon, or the whole sentence when there is no figure. */
  lead: string
  /** The peak value and its hour, or null without a model forecast. */
  figure: string | null
}

export function headlineParts(
  deliveryDay: string,
  points: readonly ForecastPoint[],
): HeadlineParts {
  const peak = forecastPeak(points)
  const day = dayLong(deliveryDay)
  if (peak === null) {
    return { lead: `No model forecast for ${day}`, figure: null }
  }
  // A repeated hour (02:00 on the autumn clock-change day) is named with its offset.
  const at = hourLabels(points.map((p) => p.ts))[points.findIndex((p) => p.ts === peak.ts)] ?? ''
  return { lead: `Forecast peak for ${day}:`, figure: `${formatGW(peak.value)} at ${at}` }
}

export function headline(deliveryDay: string, points: readonly ForecastPoint[]): string {
  const { lead, figure } = headlineParts(deliveryDay, points)
  return figure === null ? lead : `${lead} ${figure}`
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

function positiveInteger(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value > 0 ? value : null
}

/** The backtest method from the run config; parts whose keys are missing are left out. */
export function methodSentence(backtest: BacktestMetrics): string {
  const { config } = backtest
  const cutoff = config.feature_cutoff_hours_before_origin
  const cutoffHour =
    typeof cutoff === 'number' && Number.isInteger(cutoff) && cutoff >= 0 && cutoff <= 12
      ? 12 - cutoff
      : null
  const retrain = positiveInteger(config.retrain_every)
  const using =
    cutoffHour === null
      ? ''
      : `, using only hours that ended by ${String(cutoffHour).padStart(2, '0')}:00`
  const first =
    `Delivery days from ${dayPlain(backtest.first_day)} to ${dayPlain(backtest.last_day)} ` +
    `are forecast at 12:00 (Europe/Berlin) on the day before${using}.`
  const second =
    retrain === null
      ? ' The model is retrained regularly on all earlier complete days.'
      : ` The model is retrained every ${String(retrain)} ${retrain === 1 ? 'day' : 'days'} on all earlier complete days.`
  return first + second
}

/** Comparison on the hours that also have a SMARD forecast, if the run scored it. */
export function tsoHoursSentence(backtest: BacktestMetrics): string | null {
  const model = findMetric(backtest, 'lightgbm', 'overall_tso_hours', 'all')
  const baseline = findMetric(backtest, 'baseline_week', 'overall_tso_hours', 'all')
  const tso = findMetric(backtest, 'tso', 'overall', 'all')
  if (!model || !baseline || !tso) {
    return null
  }
  return (
    `On the ${model.n.toLocaleString('en-GB')} hours that also have a SMARD forecast: ` +
    `LightGBM ${formatPercent(model.mape)}, baseline ${formatPercent(baseline.mape)}, ` +
    `SMARD forecast ${formatPercent(tso.mape)}.`
  )
}
