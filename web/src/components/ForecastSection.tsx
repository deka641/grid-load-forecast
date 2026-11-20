import { useCallback, useState } from 'react'

import type { ForecastDay, ForecastPoint } from '../api/client'
import { useForecast, useForecastDays } from '../api/queries'
import { Chart } from '../charts/Chart'
import { FORECAST_SERIES, LINE_STYLES, forecastOption } from '../charts/options'
import type { Palette } from '../charts/palette'
import { NOT_AVAILABLE, formatGW, formatPercent } from '../lib/format'
import { MODELS, dayMape, hasActuals, headline } from '../lib/metrics'
import { dayShort, offsetLabel, wallClock } from '../lib/time'
import { Legend } from './Legend'
import { Loading, QueryProblem } from './Notice'
import { NO_FORECAST } from './messages'

const LEGEND = FORECAST_SERIES.map((s) => ({
  key: s.key,
  label: s.label,
  pattern: LINE_STYLES[s.key].pattern,
  width: LINE_STYLES[s.key].width,
}))

function DaySelector({
  days,
  current,
  onSelect,
}: {
  days: readonly string[]
  current: string
  onSelect: (day: string) => void
}) {
  const index = days.indexOf(current)
  const older = index >= 0 ? days[index + 1] : undefined
  const newer = index > 0 ? days[index - 1] : undefined
  const options = index >= 0 ? days : [current, ...days]
  return (
    <div className="day-selector">
      <button
        type="button"
        className="button"
        disabled={older === undefined}
        onClick={
          older === undefined
            ? undefined
            : () => {
                onSelect(older)
              }
        }
      >
        Previous day
      </button>
      <label className="day-select">
        <span className="visually-hidden">Delivery day</span>
        <select
          value={current}
          onChange={(event) => {
            onSelect(event.target.value)
          }}
        >
          {options.map((day) => (
            <option key={day} value={day}>
              {dayShort(day)}
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        className="button"
        disabled={newer === undefined}
        onClick={
          newer === undefined
            ? undefined
            : () => {
                onSelect(newer)
              }
        }
      >
        Next day
      </button>
    </div>
  )
}

function DayErrors({ points }: { points: readonly ForecastPoint[] }) {
  if (!hasActuals(points)) {
    return (
      <p className="day-errors">
        <span className="day-errors-label">This day</span>
        <span className="muted">No actual load published yet, so the error is not known.</span>
      </p>
    )
  }
  const errors = MODELS.map((m) => ({ ...m, error: dayMape(points, m.key) }))
  const hours = Math.max(...errors.map((e) => e.error?.hours ?? 0))
  return (
    <div className="day-errors">
      <span className="day-errors-label">This day</span>
      <dl className="day-errors-list">
        {errors.map((e) => (
          <div key={e.key}>
            <dt>{e.short}</dt>
            <dd>{e.error ? formatPercent(e.error.mape) : NOT_AVAILABLE}</dd>
          </div>
        ))}
      </dl>
      <span className="muted">
        MAPE against the actual load over {String(hours)} of {String(points.length)} hours
      </span>
    </div>
  )
}

function ForecastTable({ forecast }: { forecast: ForecastDay }) {
  const text = (value: number | null) => (value === null ? NOT_AVAILABLE : formatGW(value))
  return (
    <table>
      <caption>Hourly load for {dayShort(forecast.delivery_day)} in GW</caption>
      <thead>
        <tr>
          <th scope="col">Hour</th>
          {FORECAST_SERIES.map((s) => (
            <th key={s.key} scope="col">
              {s.label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {forecast.points.map((point) => (
          <tr key={point.ts}>
            <th scope="row">
              {wallClock(point.ts)} ({offsetLabel(point.ts)})
            </th>
            {FORECAST_SERIES.map((s) => (
              <td key={s.key}>{text(point[s.key])}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function ForecastView({
  forecast,
  days,
  onSelect,
}: {
  forecast: ForecastDay
  days: readonly string[]
  onSelect: (day: string) => void
}) {
  const { points } = forecast
  const build = useCallback((palette: Palette) => forecastOption(points, palette), [points])
  const hours = points.length
  return (
    <>
      <p className="headline">{headline(forecast.delivery_day, points)}</p>
      <div className="forecast-controls">
        <DaySelector days={days} current={forecast.delivery_day} onSelect={onSelect} />
        {hours === 24 ? null : (
          <p className="clock-change">
            Clock change: this day has {String(hours)} hours
            {hours === 25 ? ', so 02:00 appears twice' : ', 02:00 does not exist'}.
          </p>
        )}
      </div>
      <Legend items={LEGEND} label="Lines in the forecast chart" />
      <Chart
        build={build}
        label={`Line chart of the grid load on ${dayShort(forecast.delivery_day)} in GW by local hour: actual, LightGBM model, baseline and SMARD forecast`}
        className="chart-forecast"
      >
        <ForecastTable forecast={forecast} />
      </Chart>
      <DayErrors points={points} />
    </>
  )
}

export function ForecastSection() {
  const [day, setDay] = useState<string | undefined>(undefined)
  const days = useForecastDays(true)
  const forecast = useForecast(day, true)

  let body
  if (forecast.isPending) {
    body = <Loading what="forecast" />
  } else if (forecast.isError) {
    body = (
      <QueryProblem
        what="the forecast"
        error={forecast.error}
        notFound={NO_FORECAST}
        onRetry={() => void forecast.refetch()}
      />
    )
  } else {
    body = <ForecastView forecast={forecast.data} days={days.data?.days ?? []} onSelect={setDay} />
  }

  return (
    <section className="section" aria-labelledby="forecast-title">
      <h2 id="forecast-title" className="section-title">
        Day-ahead forecast
      </h2>
      {body}
    </section>
  )
}
