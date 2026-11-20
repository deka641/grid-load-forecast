/**
 * Deterministic, realistic API responses for the browser checks. Nothing here talks
 * to SMARD or the real API.
 */

const BERLIN = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Europe/Berlin',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
  timeZoneName: 'longOffset',
})

interface LocalParts {
  day: string
  hour: number
  minute: number
  offset: string
}

function local(instant: number): LocalParts {
  const parts: Record<string, string> = {}
  for (const part of BERLIN.formatToParts(new Date(instant))) {
    parts[part.type] = part.value
  }
  const offset = (parts.timeZoneName ?? 'GMT+01:00').replace('GMT', '') || '+00:00'
  return {
    day: `${parts.year ?? ''}-${parts.month ?? ''}-${parts.day ?? ''}`,
    hour: Number(parts.hour),
    minute: Number(parts.minute),
    offset,
  }
}

export function isoLocal(instant: number): string {
  const p = local(instant)
  const hh = String(p.hour).padStart(2, '0')
  const mm = String(p.minute).padStart(2, '0')
  return `${p.day}T${hh}:${mm}:00${p.offset}`
}

const HOUR = 3_600_000

/** UTC instants of every local hour of a Berlin day: 23, 24 or 25. */
export function hoursOfDay(day: string): number[] {
  const [y, m, d] = day.split('-').map(Number)
  let start = Date.UTC(y ?? 2026, (m ?? 1) - 1, d ?? 1) - 3 * HOUR
  while (local(start).day !== day) {
    start += HOUR
  }
  const hours: number[] = []
  for (let t = start; local(t).day === day; t += HOUR) {
    hours.push(t)
  }
  return hours
}

export function addDays(day: string, delta: number): string {
  const [y, m, d] = day.split('-').map(Number)
  const date = new Date(Date.UTC(y ?? 2026, (m ?? 1) - 1, (d ?? 1) + delta))
  return date.toISOString().slice(0, 10)
}

/** Small deterministic noise in [-1, 1]. */
function noise(seed: number): number {
  const x = Math.sin(seed * 12.9898) * 43758.5453
  return (x - Math.floor(x)) * 2 - 1
}

const PROFILE = [
  46, 44.5, 43.5, 43, 43.5, 45.5, 51, 58, 62, 63.5, 64, 63.5, 62.5, 62, 62, 63, 65.5, 68.5, 68, 66,
  62.5, 58.5, 54, 49.5,
]

function weekdayFactor(day: string): number {
  const dow = new Date(`${day}T12:00:00Z`).getUTCDay()
  if (dow === 0) return 0.82
  if (dow === 6) return 0.88
  return 1
}

/** Grid load in MWh per hour for a local hour. */
export function load(instant: number): number {
  const p = local(instant)
  const base = PROFILE[p.hour] ?? 55
  return Math.round((base * weekdayFactor(p.day) + noise(instant / HOUR) * 0.8) * 1000)
}

export const TODAY = '2026-11-20'
export const LATEST_DAY = '2026-11-21'
export const NOW = Date.parse('2026-11-20T13:02:00Z')

export function forecastDays(): string[] {
  const days: string[] = []
  for (let day = LATEST_DAY; day >= '2026-03-20'; day = addDays(day, -1)) {
    days.push(day)
  }
  return days
}

export function forecastDay(day: string) {
  const hasActual = day <= TODAY
  return {
    delivery_day: day,
    unit: 'MWh',
    runs: {
      lightgbm: {
        run_id: `fc-${day}`,
        issued_at: isoLocal(Date.parse(`${addDays(day, -1)}T11:00:00Z`)),
        created_at: isoLocal(Date.parse(`${addDays(day, -1)}T11:04:00Z`)),
      },
    },
    points: hoursOfDay(day).map((t) => {
      const actual = load(t)
      const seed = t / HOUR
      const visible = hasActual && t + HOUR <= NOW
      return {
        ts: isoLocal(t),
        lightgbm: Math.round(actual * (1 + noise(seed + 1) * 0.025)),
        baseline_week: Math.round(load(t - 7 * 24 * HOUR) * (1 + noise(seed + 2) * 0.02)),
        tso: Math.round(actual * (1 + noise(seed + 3) * 0.03)),
        actual: visible ? actual : null,
      }
    }),
  }
}

const SERIES = [
  ['load', 'Grid load', 'MWh', 'load'],
  ['load_forecast_tso', 'Grid load forecast (SMARD)', 'MWh', 'forecast'],
  ['gen_lignite', 'Lignite', 'MWh', 'generation'],
  ['gen_nuclear', 'Nuclear', 'MWh', 'generation'],
  ['gen_wind_offshore', 'Wind offshore', 'MWh', 'generation'],
  ['gen_hydro', 'Hydropower', 'MWh', 'generation'],
  ['gen_other_conventional', 'Other conventional', 'MWh', 'generation'],
  ['gen_other_renewable', 'Other renewables', 'MWh', 'generation'],
  ['gen_biomass', 'Biomass', 'MWh', 'generation'],
  ['gen_wind_onshore', 'Wind onshore', 'MWh', 'generation'],
  ['gen_solar', 'Photovoltaics', 'MWh', 'generation'],
  ['gen_hard_coal', 'Hard coal', 'MWh', 'generation'],
  ['gen_pumped_storage', 'Pumped storage', 'MWh', 'generation'],
  ['gen_gas', 'Natural gas', 'MWh', 'generation'],
  ['price_day_ahead', 'Day-ahead price DE/LU', 'EUR/MWh', 'price'],
] as const

export function seriesCatalog(withPrice = true) {
  return {
    series: SERIES.filter((s) => withPrice || s[0] !== 'price_day_ahead').map(
      ([key, label, unit, category]) => ({ key, label, unit, category }),
    ),
  }
}

function wind(instant: number): number {
  const h = instant / HOUR
  // A windy front mid-week and a calm weekend in the window.
  return Math.max(2, 18 + 14 * Math.sin(h / 19) + 6 * Math.sin(h / 5.3) + noise(h) * 1.5)
}

function solar(p: LocalParts): number {
  const x = (p.hour + 0.5 - 12.3) / 3.1
  return p.hour < 8 || p.hour > 16 ? 0 : Math.max(0, 11 * Math.exp(-x * x))
}

function generation(key: string, instant: number): number {
  const p = local(instant)
  const h = instant / HOUR
  const demand = load(instant) / 1000
  const w = wind(instant)
  const s = solar(p)
  const residual = Math.max(8, demand - w - s - 6.5)
  const value: Record<string, number> = {
    gen_nuclear: 0,
    gen_wind_onshore: w * 0.82,
    gen_wind_offshore: w * 0.18,
    gen_solar: s,
    gen_biomass: 4.3 + noise(h + 5) * 0.1,
    gen_hydro: 1.7 + noise(h + 6) * 0.1,
    gen_other_renewable: 0.18,
    gen_other_conventional: 1.3,
    gen_pumped_storage: p.hour >= 17 && p.hour <= 20 ? 2.5 + noise(h) : 0.3,
    gen_lignite: Math.min(15, 6 + residual * 0.3),
    gen_hard_coal: Math.min(9, 1 + residual * 0.15),
    gen_gas: Math.min(18, 2 + residual * 0.28),
  }
  return Math.round((value[key] ?? 0) * 1000)
}

function price(instant: number): number {
  const p = local(instant)
  const w = wind(instant)
  const s = solar(p)
  const peak = p.hour >= 17 && p.hour <= 20 ? 45 : 0
  const value = 128 + peak - w * 3.2 - s * 5 + noise(instant / HOUR + 9) * 6
  return Math.round(value * 100) / 100
}

export function actuals(keys: string[], start: string, end: string) {
  const hours: number[] = []
  for (let day = start; day <= end; day = addDays(day, 1)) {
    hours.push(...hoursOfDay(day))
  }
  const catalog = seriesCatalog().series
  return {
    resolution: 'hour',
    start,
    end,
    series: keys.map((key) => {
      const info = catalog.find((s) => s.key === key)
      const isPrice = key === 'price_day_ahead'
      // Prices exist for tomorrow already; measured values end with the last full hour.
      const limit = isPrice ? Date.parse(`${addDays(end, 1)}T23:00:00Z`) : NOW - HOUR
      return {
        key,
        label: info?.label ?? key,
        unit: info?.unit ?? 'MWh',
        points: hours
          .filter((t) => t <= limit)
          .map((t) => ({ ts: isoLocal(t), value: isPrice ? price(t) : generation(key, t) })),
      }
    }),
  }
}

function metric(scope_value: string, mae: number, mape: number, n: number) {
  return { scope_value, mae, mape, n }
}

function modelMetrics(
  level: number,
  mae: number,
  hourShape: (h: number) => number,
  hours = 8_736,
  tsoHours?: number,
) {
  return {
    overall: [metric('all', mae, level, hours)],
    ...(tsoHours === undefined
      ? {}
      : { overall_tso_hours: [metric('all', mae * 0.99, level * 0.99, tsoHours)] }),
    day_type: [
      metric('workday', mae * 0.95, level * 0.93, 11_136),
      metric('saturday', mae * 1.02, level * 1.08, 2_304),
      metric('sunday_holiday', mae * 1.2, level * 1.31, 2_592),
    ],
    hour: Array.from({ length: 24 }, (_, h) =>
      metric(String(h), mae * hourShape(h), level * hourShape(h), 668),
    ),
  }
}

export function backtest() {
  const morning = (h: number) => 0.85 + 0.35 * Math.exp(-(((h - 7) / 1.8) ** 2))
  const evening = (h: number) => morning(h) + 0.2 * Math.exp(-(((h - 18) / 2) ** 2))
  return {
    run_id: 'bt-2026-11-16',
    created_at: '2026-11-16T04:12:00+01:00',
    first_day: '2025-11-16',
    last_day: '2026-11-15',
    config: {
      days: 365,
      retrain_every: 7,
      training_start: '2019-01-01',
      origin: '12:00 Europe/Berlin on the day before delivery',
      feature_cutoff_hours_before_origin: 2,
      seed: 42,
    },
    metrics: {
      lightgbm: modelMetrics(2.41, 1312, evening, 8_736, 8_712),
      baseline_week: modelMetrics(5.87, 3205, (h) => 0.9 + 0.2 * Math.sin(h / 3.8), 8_736, 8_712),
      tso: modelMetrics(2.78, 1496, morning, 8_712),
    },
  }
}

/** The API before its first snapshot: 200 with initialised false and no runs. */
export function statusBeforeFirstSnapshot() {
  return {
    ...status(),
    initialised: false,
    snapshot_created_at: null,
    schema_version: null,
    series: seriesCatalog().series.map((s) => ({
      key: s.key,
      label: s.label,
      last_ts: null,
      age_seconds: null,
    })),
    last_load_run: null,
    last_forecast: null,
  }
}

export function status(ageSeconds = 1920) {
  const lastLoad = NOW - ageSeconds * 1000 - 15 * 60_000
  const keys = seriesCatalog().series
  return {
    initialised: true,
    generated_at: isoLocal(NOW),
    snapshot_created_at: isoLocal(NOW - 6 * 60_000),
    snapshot_error: null,
    schema_version: 3,
    series: keys.map((s) => ({
      key: s.key,
      label: s.label,
      last_ts: isoLocal(lastLoad),
      age_seconds: ageSeconds,
    })),
    last_load_run: {
      run_id: 'load-2026-11-20T0950',
      mode: 'incremental',
      status: 'succeeded',
      started_at: isoLocal(NOW - 14 * 60_000),
      finished_at: isoLocal(NOW - 11 * 60_000),
    },
    last_forecast: {
      model: 'lightgbm',
      delivery_day: LATEST_DAY,
      issued_at: '2026-11-20T12:00:00+01:00',
    },
    attribution: 'Bundesnetzagentur | SMARD.de',
    licence: 'CC BY 4.0',
    licence_url: 'https://creativecommons.org/licenses/by/4.0/',
    price_note:
      'The Bundesnetzagentur reviews the licensing of the wholesale electricity prices ' +
      'shown on SMARD.de in its HEDWIG procedure. Until that review is concluded, price ' +
      'data is shown with that reservation, and price ingestion can be switched off.',
  }
}
