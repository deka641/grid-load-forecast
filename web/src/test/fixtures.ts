import type {
  Actuals,
  BacktestMetrics,
  ForecastDay,
  ForecastPoint,
  SeriesCatalog,
  Status,
} from '../api/client'

const pad = (n: number) => String(n).padStart(2, '0')

/** Wall-clock hours with their offsets for normal and clock-change days. */
export function dayHours(day: string): string[] {
  if (day === '2026-10-25') {
    return [
      `${day}T00:00:00+02:00`,
      `${day}T01:00:00+02:00`,
      `${day}T02:00:00+02:00`,
      `${day}T02:00:00+01:00`,
      ...Array.from({ length: 21 }, (_, i) => `${day}T${pad(i + 3)}:00:00+01:00`),
    ]
  }
  if (day === '2026-03-29') {
    return [
      `${day}T00:00:00+01:00`,
      `${day}T01:00:00+01:00`,
      ...Array.from({ length: 21 }, (_, i) => `${day}T${pad(i + 3)}:00:00+02:00`),
    ]
  }
  return Array.from({ length: 24 }, (_, i) => `${day}T${pad(i)}:00:00+01:00`)
}

export function forecast(day: string, withActual = true): ForecastDay {
  const points: ForecastPoint[] = dayHours(day).map((ts, i) => {
    const actual = 50_000 + i * 500
    return {
      ts,
      lightgbm: actual * 1.02,
      baseline_week: actual * 0.95,
      tso: actual,
      actual: withActual ? actual : null,
    }
  })
  return { delivery_day: day, unit: 'MWh', runs: {}, points }
}

export const DAYS = ['2026-11-21', '2026-11-20', '2026-11-19', '2026-10-25', '2026-03-29']

export function status(overrides: Partial<Status> = {}): Status {
  return {
    initialised: true,
    generated_at: '2026-11-20T14:02:00+01:00',
    snapshot_created_at: '2026-11-20T13:56:00+01:00',
    schema_version: 3,
    series: [
      {
        key: 'load',
        label: 'Grid load',
        last_ts: '2026-11-20T13:15:00+01:00',
        age_seconds: 1920,
      },
      {
        key: 'load_forecast_tso',
        label: 'Grid load forecast (SMARD)',
        last_ts: '2026-11-21T23:45:00+01:00',
        age_seconds: -100_000,
      },
    ],
    last_load_run: {
      run_id: 'r1',
      mode: 'incremental',
      status: 'succeeded',
      started_at: '2026-11-20T13:48:00+01:00',
      finished_at: '2026-11-20T13:51:00+01:00',
    },
    last_forecast: null,
    attribution: 'Bundesnetzagentur | SMARD.de',
    licence: 'CC BY 4.0',
    licence_url: 'https://creativecommons.org/licenses/by/4.0/',
    price_note:
      'The Bundesnetzagentur reviews the licensing of the wholesale electricity prices shown on SMARD.de in its HEDWIG procedure.',
    ...overrides,
  }
}

export function catalog(withPrice = true): SeriesCatalog {
  const series = [
    { key: 'load', label: 'Grid load', unit: 'MWh', category: 'load' },
    { key: 'gen_solar', label: 'Photovoltaics', unit: 'MWh', category: 'generation' },
    { key: 'gen_wind_onshore', label: 'Wind onshore', unit: 'MWh', category: 'generation' },
  ]
  if (withPrice) {
    series.push({
      key: 'price_day_ahead',
      label: 'Day-ahead price DE/LU',
      unit: 'EUR/MWh',
      category: 'price',
    })
  }
  return { series }
}

export function actuals(keys: string[]): Actuals {
  const stamps = ['2026-11-19T23:00:00+01:00', '2026-11-20T00:00:00+01:00']
  return {
    resolution: 'hour',
    start: '2026-11-14',
    end: '2026-11-20',
    series: keys.map((key) => ({
      key,
      label: key,
      unit: key === 'price_day_ahead' ? 'EUR/MWh' : 'MWh',
      points:
        key === 'price_day_ahead'
          ? [
              { ts: stamps[0] ?? '', value: -5.5 },
              { ts: stamps[1] ?? '', value: 80 },
              { ts: '2026-11-21T00:00:00+01:00', value: 90 },
            ]
          : stamps.map((ts, i) => ({ ts, value: 10_000 + i * 1000 })),
    })),
  }
}

export function backtest(): BacktestMetrics {
  const scopes = (level: number) => ({
    overall: [{ scope_value: 'all', mae: level * 500, mape: level, n: 16_032 }],
    day_type: [
      { scope_value: 'workday', mae: 1, mape: level - 0.2, n: 1 },
      { scope_value: 'saturday', mae: 1, mape: level + 0.2, n: 1 },
    ],
    hour: [
      { scope_value: '10', mae: 1, mape: level + 1, n: 1 },
      { scope_value: '2', mae: 1, mape: level - 1, n: 1 },
    ],
  })
  return {
    run_id: 'bt',
    created_at: '2026-11-16T04:00:00+01:00',
    first_day: '2025-01-06',
    last_day: '2026-11-15',
    config: { retrain_every: 7, feature_cutoff_hours_before_origin: 2, days: 365 },
    metrics: { lightgbm: scopes(2.4), baseline_week: scopes(5.9) },
  }
}
