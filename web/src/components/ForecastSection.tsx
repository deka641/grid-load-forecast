import { useCallback, useState } from 'react'

import type { ForecastDay, ForecastPoint } from '../api/client'
import { useForecast, useForecastDays } from '../api/queries'
import { Chart } from '../charts/LazyChart'
import { FORECAST_SERIES, LINE_STYLES, forecastOption } from '../charts/options'
import type { Palette } from '../charts/palette'
import { NOT_AVAILABLE, formatGW, formatPercent } from '../lib/format'
import { MODELS, dayErrors, hasActuals, headline } from '../lib/metrics'
import { neighbours } from '../lib/days'
import { dayShort, offsetLabel, wallClock } from '../lib/time'
import { Legend } from './Legend'
import { Loading, QueryProblem } from './Notice'
import { NO_FORECAST, noForecastFor } from './messages'

function legendItems(withActual: boolean) {
  return FORECAST_SERIES.map((s) => ({
    key: s.key,
    label: s.label,
    pattern: LINE_STYLES[s.key].pattern,
    width: LINE_STYLES[s.key].width,
    empty: s.key === 'actual' && !withActual,
  }))
}

function DaySelector({
  days,
  current,
  onSelect,
}: {
  days: readonly string[]
  current: string
  onSelect: (day: string) => void
}) {
  const { older, newer } = neighbours(days, current)
  const options = days.includes(current) ? days : [current, ...days]
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

function DayErrorsRow({ points }: { points: readonly ForecastPoint[] }) {
  if (!hasActuals(points)) {
    return (
      <p className="day-errors">
        <span className="day-errors-label">This day</span>
        <span className="muted">No actual load published yet, so the error is not known.</span>
      </p>
    )
  }
  const errors = dayErrors(points)
  const hours = errors.lightgbm?.hours ?? 0
  const tsoHours = errors.tso?.hours ?? 0
  return (
    <div className="day-errors">
      <span className="day-errors-label">This day</span>
      <dl className="day-errors-list">
        {MODELS.map((m) => {
          const error = errors[m.key]
          return (
            <div key={m.key}>
              <dt>{m.short}</dt>
              <dd>{error ? formatPercent(error.mape) : NOT_AVAILABLE}</dd>
            </div>
          )
        })}
      </dl>
      <span className="muted">
        MAPE against the actual load over {String(hours)} of {String(points.length)} hours
        {tsoHours === hours ? '' : `; SMARD forecast over ${String(tsoHours)} hours`}
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

function ForecastView({ forecast }: { forecast: ForecastDay }) {
  const { points } = forecast
  const build = useCallback((palette: Palette) => forecastOption(points, palette), [points])
  const hours = points.length
  return (
    <>
      {hours === 24 ? null : (
        <p className="clock-change">
          Clock change: this day has {String(hours)} hours
          {hours === 25 ? ', so 02:00 appears twice' : ', 02:00 does not exist'}.
        </p>
      )}
      <Legend items={legendItems(hasActuals(points))} label="Lines in the forecast chart" />
      <Chart
        build={build}
        label={`Line chart of the grid load on ${dayShort(forecast.delivery_day)} in GW by local hour: actual, LightGBM model, baseline and SMARD forecast`}
        className="chart-forecast"
      >
        <ForecastTable forecast={forecast} />
      </Chart>
      <DayErrorsRow points={points} />
    </>
  )
}

export function ForecastSection() {
  // undefined: the newest day, which is the first of the day list once that is known.
  const [day, setDay] = useState<string | undefined>(undefined)
  const days = useForecastDays(true)
  const list = days.data?.days ?? []
  // Ask for the newest listed day by date, so a later explicit request hits the same cache entry.
  const requested = day ?? list[0]
  const forecast = useForecast(requested, day !== undefined || !days.isPending)

  let body
  if (forecast.isPending) {
    body = <Loading what="forecast" />
  } else if (forecast.isError) {
    body = (
      <QueryProblem
        what="the forecast"
        error={forecast.error}
        notFound={day === undefined ? NO_FORECAST : noForecastFor(day)}
        onRetry={() => void forecast.refetch()}
      />
    )
  } else {
    body = <ForecastView forecast={forecast.data} />
  }

  // The selector follows the requested day, not placeholder data still on screen.
  const shownDay = requested ?? forecast.data?.delivery_day
  return (
    <section className="section" aria-labelledby="forecast-title">
      <h2 id="forecast-title" className="section-title">
        Day-ahead forecast
      </h2>
      {forecast.data && !forecast.isError ? (
        <p className="headline">{headline(forecast.data.delivery_day, forecast.data.points)}</p>
      ) : null}
      <div className="forecast-controls">
        {shownDay === undefined ? null : (
          <DaySelector days={list} current={shownDay} onSelect={setDay} />
        )}
        {days.isError ? (
          <p className="inline-note">
            The list of forecast days could not be loaded.{' '}
            <button type="button" className="link-button" onClick={() => void days.refetch()}>
              Retry
            </button>
          </p>
        ) : null}
      </div>
      <div className="forecast-body" aria-busy={forecast.isPlaceholderData}>
        {body}
      </div>
    </section>
  )
}
