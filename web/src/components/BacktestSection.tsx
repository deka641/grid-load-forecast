import { useCallback, useEffect, useRef, useState } from 'react'

import type { BacktestMetrics } from '../api/client'
import { useBacktest } from '../api/queries'
import { Chart } from '../charts/LazyChart'
import { LINE_STYLES, hourLabel, mapeByHourOption } from '../charts/options'
import type { Palette } from '../charts/palette'
import { NOT_AVAILABLE, formatPercent } from '../lib/format'
import { MODELS, findMetric, mapeByHour, methodSentence, tsoHoursSentence } from '../lib/metrics'
import { Legend } from './Legend'
import { Loading, QueryProblem } from './Notice'
import { NO_BACKTEST } from './messages'

const DAY_TYPES = [
  { value: 'workday', label: 'Workdays' },
  { value: 'saturday', label: 'Saturdays' },
  { value: 'sunday_holiday', label: 'Sundays and holidays' },
] as const

const LEGEND = MODELS.map((m) => ({
  key: m.key,
  label: m.short,
  pattern: LINE_STYLES[m.key].pattern,
  width: LINE_STYLES[m.key].width,
}))

/** True while the element's content is wider than its box, so that it scrolls sideways. */
function useOverflowsX<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [overflows, setOverflows] = useState(false)
  useEffect(() => {
    const element = ref.current
    if (!element) {
      return
    }
    const measure = () => {
      setOverflows(element.scrollWidth > element.clientWidth)
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    if (element.firstElementChild) {
      observer.observe(element.firstElementChild)
    }
    return () => {
      observer.disconnect()
    }
  }, [])
  return [ref, overflows] as const
}

function MetricsTable({ backtest }: { backtest: BacktestMetrics }) {
  const [box, overflows] = useOverflowsX<HTMLDivElement>()
  const mape = (model: (typeof MODELS)[number]['key'], scope: string, value: string) => {
    const metric = findMetric(backtest, model, scope, value)
    return metric ? formatPercent(metric.mape) : NOT_AVAILABLE
  }
  return (
    // Only a box that scrolls sideways is a keyboard stop and a named region.
    <div
      ref={box}
      className="table-scroll"
      {...(overflows ? { tabIndex: 0, role: 'region', 'aria-label': 'Backtest errors table' } : {})}
    >
      <table className="metrics">
        <caption className="visually-hidden">
          Backtest errors by forecast: mean absolute error in GW, number of scored hours and mean
          absolute percentage error by day type
        </caption>
        <thead>
          <tr>
            <td rowSpan={2} />
            <th scope="col" rowSpan={2} className="num">
              MAE
            </th>
            <th scope="col" rowSpan={2} className="num">
              Hours
            </th>
            <th scope="colgroup" colSpan={4} className="group wide-only">
              MAPE
            </th>
          </tr>
          <tr>
            <th scope="col" className="num">
              <span className="label-long">
                <span className="visually-hidden">MAPE,</span> All days
              </span>
              <span className="label-short" aria-hidden="true">
                MAPE
              </span>
            </th>
            {DAY_TYPES.map((t) => (
              <th key={t.value} scope="col" className="num wide-only">
                <span className="visually-hidden">MAPE,</span> {t.label}
              </th>
            ))}
          </tr>
        </thead>
        {MODELS.map((m) => {
          const overall = findMetric(backtest, m.key, 'overall', 'all')
          return (
            <tbody key={m.key}>
              <tr>
                <th scope="row">
                  <span className={`row-key stroke-${m.key}`} aria-hidden="true" />
                  <span className="label-long">{m.label}</span>
                  <span className="label-short" aria-hidden="true">
                    {m.short}
                  </span>
                </th>
                <td className="num">
                  {overall ? `${(overall.mae / 1000).toFixed(2)} GW` : NOT_AVAILABLE}
                </td>
                <td className="num metric-hours">
                  {overall ? overall.n.toLocaleString('en-GB') : NOT_AVAILABLE}
                </td>
                <td className="num">{mape(m.key, 'overall', 'all')}</td>
                {DAY_TYPES.map((t) => (
                  <td key={t.value} className="num wide-only">
                    {mape(m.key, 'day_type', t.value)}
                  </td>
                ))}
              </tr>
              {/* Narrow screens: the day-type columns are hidden and stacked here instead. */}
              <tr className="narrow-only day-type-row">
                <td colSpan={4}>
                  <span className="visually-hidden">MAPE by day type:</span>{' '}
                  {DAY_TYPES.map((t) => `${t.label} ${mape(m.key, 'day_type', t.value)}`).join(
                    ', ',
                  )}
                </td>
              </tr>
            </tbody>
          )
        })}
      </table>
    </div>
  )
}

function HourTable({ backtest }: { backtest: BacktestMetrics }) {
  const rows = MODELS.map((m) => ({ ...m, values: mapeByHour(backtest, m.key) }))
  return (
    <table>
      <caption>MAPE in percent by local hour of the day</caption>
      <thead>
        <tr>
          <th scope="col">Hour</th>
          {rows.map((r) => (
            <th key={r.key} scope="col">
              {r.short}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {Array.from({ length: 24 }, (_, hour) => (
          <tr key={hour}>
            <th scope="row">{hourLabel(hour)}</th>
            {rows.map((r) => {
              const value = r.values[hour]
              return (
                <td key={r.key}>
                  {value === null || value === undefined ? NOT_AVAILABLE : formatPercent(value)}
                </td>
              )
            })}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function BacktestView({ backtest }: { backtest: BacktestMetrics }) {
  const build = useCallback((palette: Palette) => mapeByHourOption(backtest, palette), [backtest])
  const hours = findMetric(backtest, 'lightgbm', 'overall', 'all')?.n
  const tsoSentence = tsoHoursSentence(backtest)
  return (
    <>
      <p className="prose">
        {methodSentence(backtest)} Errors are measured against the actual grid load
        {hours === undefined ? '' : ` over ${hours.toLocaleString('en-GB')} hours`}.
      </p>
      <div className="backtest-grid">
        <div>
          <MetricsTable backtest={backtest} />
          {tsoSentence === null ? null : <p className="note">{tsoSentence}</p>}
          <p className="note">
            The SMARD forecast is scored on its values as published today. Its publication time is
            not known, so it may include revisions made after 12:00 on the day before. The
            model&apos;s inputs are the load values as stored today, including later corrections it
            would not have had at the cutoff, which can favour the model.
          </p>
        </div>
        <div className="backtest-hours">
          <h3 className="subsection-title">MAPE by hour of day</h3>
          <Legend items={LEGEND} label="Lines in the hourly error chart" />
          <Chart
            build={build}
            label="Line chart of the mean absolute percentage error by local hour of the day for the LightGBM model, the baseline and the SMARD forecast"
            className="chart-small"
            dataLabel="MAPE by hour table"
          >
            <HourTable backtest={backtest} />
          </Chart>
          <p className="note">
            SMARD forecast values per hour are computed on the hours where SMARD publishes a value.
          </p>
        </div>
      </div>
    </>
  )
}

export function BacktestSection() {
  const backtest = useBacktest(true)
  let body
  if (backtest.isPending) {
    body = <Loading what="backtest" reserve="backtest" />
  } else if (backtest.isError) {
    body = (
      <QueryProblem
        what="the backtest"
        error={backtest.error}
        notFound={NO_BACKTEST}
        onRetry={() => void backtest.refetch()}
      />
    )
  } else {
    body = <BacktestView backtest={backtest.data} />
  }
  return (
    <section className="section" aria-labelledby="backtest-title">
      <h2 id="backtest-title" className="section-title">
        How accurate is it?
      </h2>
      {body}
    </section>
  )
}
