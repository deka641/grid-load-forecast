import { useCallback, useMemo } from 'react'

import type { Actuals, SeriesCatalog } from '../api/client'
import { useActuals, useSeries, useStatus } from '../api/queries'
import { Chart } from '../charts/LazyChart'
import { isShaded, mixOption, newestDueDay, priceGaps, priceOption } from '../charts/options'
import type { ChartLayout, TimeSeries } from '../charts/options'
import { GENERATION, generationColor } from '../charts/palette'
import type { Palette } from '../charts/palette'
import { NOT_AVAILABLE } from '../lib/format'
import { PRICE_KEY, alignSeries, meanOf, stats } from '../lib/series'
import type { Aligned } from '../lib/series'
import { coversFullDays } from '../lib/days'
import {
  addDays,
  berlinParts,
  dayPlain,
  dayShort,
  localDay,
  offsetLabel,
  wallClock,
} from '../lib/time'
import { Legend } from './Legend'
import { Loading, QueryProblem } from './Notice'

const DAYS = 7

function generationSeries(catalog: SeriesCatalog, aligned: Aligned): TimeSeries[] {
  const labels = new Map(catalog.series.map((s) => [s.key, s.label]))
  return (
    GENERATION.filter((g) => labels.has(g.key))
      .map((g) => ({
        key: g.key,
        label: labels.get(g.key) ?? g.key,
        values: (aligned.values[g.key] ?? []).map((v) => (v === null ? null : v / 1000)),
      }))
      // A source without any value in the range (nuclear since 2023) is left out.
      .filter((s) => s.values.some((v) => v !== null))
  )
}

function MixSummary({ series }: { series: readonly TimeSeries[] }) {
  const means = series.map((s) => ({ ...s, mean: meanOf(s.values) }))
  const total = means.reduce((sum, s) => sum + (s.mean ?? 0), 0)
  return (
    <table>
      <caption>Average generation by source over the period</caption>
      <thead>
        <tr>
          <th scope="col">Source</th>
          <th scope="col">Average in GW</th>
          <th scope="col">Share in percent</th>
        </tr>
      </thead>
      <tbody>
        {[...means].reverse().map((s) => (
          <tr key={s.key}>
            <th scope="row">{s.label}</th>
            <td>{s.mean === null ? NOT_AVAILABLE : s.mean.toFixed(1)}</td>
            <td>
              {s.mean === null || total === 0 ? NOT_AVAILABLE : ((s.mean / total) * 100).toFixed(1)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function PriceTable({
  stamps,
  values,
}: {
  stamps: readonly string[]
  values: readonly (number | null)[]
}) {
  return (
    <table>
      <caption>Hourly day-ahead price DE/LU in EUR/MWh</caption>
      <thead>
        <tr>
          <th scope="col">Hour</th>
          <th scope="col">Price</th>
        </tr>
      </thead>
      <tbody>
        {stamps.map((ts, i) => {
          const value = values[i] ?? null
          return (
            <tr key={ts}>
              <th scope="row">
                {dayShort(localDay(ts), false)}, {wallClock(ts)} ({offsetLabel(ts)})
              </th>
              <td>{value === null ? NOT_AVAILABLE : value.toFixed(2)}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function hourText(ts: string, withOffset: boolean): string {
  const text = `${dayShort(localDay(ts), false)}, ${wallClock(ts)}`
  return withOffset ? `${text} (${offsetLabel(ts)})` : text
}

/** Berlin day, wall clock and offset of the end of the hour that starts at `ts`. */
function hourEnd(ts: string): { day: string; time: string; offset: string } {
  const end = Date.parse(ts) + 3_600_000
  const { day, time } = berlinParts(new Date(end))
  const minutes = (Date.parse(`${day}T${time}:00Z`) - end) / 60_000
  const hours = String(Math.trunc(Math.abs(minutes) / 60))
  const rest = Math.abs(minutes) % 60
  const sign = minutes < 0 ? '-' : '+'
  const tail = rest === 0 ? '' : `:${String(rest).padStart(2, '0')}`
  return { day, time, offset: `UTC${sign}${hours}${tail}` }
}

/**
 * "from Thu 19 Nov, 23:00 to Fri 20 Nov, 01:00 (UTC+1)": from the start of the first hour
 * to the end of the last, the day named once for a range within one day, and both offsets
 * if they differ.
 */
function hoursText(from: string, to: string): string {
  const end = hourEnd(to)
  const both = offsetLabel(from) !== end.offset
  const endDay = end.day === localDay(from) ? '' : `${dayShort(end.day, false)}, `
  return `from ${hourText(from, both)} to ${endDay}${end.time} (${end.offset})`
}

function listText(parts: readonly string[]): string {
  return parts.length < 2
    ? (parts[0] ?? '')
    : `${parts.slice(0, -1).join(', ')} and ${parts.at(-1) ?? ''}`
}

function PriceStats({
  stamps,
  values,
  dueDay,
}: {
  stamps: readonly string[]
  values: readonly (number | null)[]
  dueDay: string | null
}) {
  const s = stats(values)
  if (s === null) {
    return <p className="chart-stats muted">No price data in this period.</p>
  }
  const gaps = priceGaps(stamps, values, dueDay)
  const source = gaps.filter((g) => g.kind === 'source')
  const sourceShaded = source.some((g) => isShaded(g, stamps.length))
  // The sentence about hours not yet published refers to the shading, so it needs one.
  const pending = gaps.find((g) => g.kind === 'pending' && isShaded(g, stamps.length))
  const missing = source.reduce((sum, g) => sum + g.to - g.from + 1, 0)
  const lastIndex =
    pending === undefined ? -1 : values.slice(0, pending.from).findLastIndex((v) => v !== null)
  const last = stamps[lastIndex]
  const sourceText = listText(
    source.map((g) => hoursText(stamps[g.from] ?? '', stamps[g.to] ?? '')),
  )
  return (
    <p className="chart-stats">
      Hourly prices (each the mean of four quarter hours) from {s.min.toFixed(2)} to{' '}
      {s.max.toFixed(2)} EUR/MWh, average {s.mean.toFixed(2)} EUR/MWh
      {missing > 0
        ? ` over ${String(s.hours)} of ${String(s.hours + missing)} hours, negative in ${String(s.negativeHours)} of them.`
        : `, negative in ${String(s.negativeHours)} of ${String(s.hours)} hours.`}{' '}
      A single negative quarter hour can be hidden in its hourly mean.
      {missing > 0
        ? ` SMARD has published no prices ${sourceText}${sourceShaded ? '; the chart shades these hours lighter' : ''}.`
        : ''}
      {last !== undefined
        ? ` The last published price is for ${hourText(last, true)}; the shaded part of the chart has no prices yet.`
        : pending !== undefined
          ? ' The shaded part of the chart has no prices yet.'
          : ''}
    </p>
  )
}

function ContextCharts({
  catalog,
  aligned,
  priced,
  showPrice,
  priceNote,
  dueDay,
  start,
  end,
}: {
  catalog: SeriesCatalog
  aligned: Aligned
  priced: Aligned
  showPrice: boolean
  priceNote: string
  dueDay: string | null
  start: string
  end: string
}) {
  const series = useMemo(() => generationSeries(catalog, aligned), [catalog, aligned])
  const prices = useMemo(() => priced.values[PRICE_KEY] ?? [], [priced])
  const buildMix = useCallback(
    (palette: Palette) =>
      mixOption(aligned.stamps, series, palette, (key) => generationColor(palette, key)),
    [aligned, series],
  )
  const buildPrice = useCallback(
    (palette: Palette, layout: ChartLayout) =>
      priceOption(priced.stamps, prices, palette, layout, dueDay),
    [priced, prices, dueDay],
  )
  const range = `${dayPlain(start)} to ${dayPlain(end)}`
  if (aligned.stamps.length === 0) {
    return <p className="notice">No generation data for {range}.</p>
  }
  return (
    <>
      <h3 className="subsection-title">Generation by source, GW</h3>
      <Legend
        items={[...series].reverse().map((s) => ({ key: s.key, label: s.label, area: true }))}
        label="Sources in the generation chart, top to bottom"
      />
      <Chart
        build={buildMix}
        label={`Stacked area chart of hourly electricity generation in Germany by source in GW, ${range}`}
        className="chart-mix"
        dataLabel="Generation table"
      >
        <MixSummary series={series} />
      </Chart>
      {showPrice ? (
        <>
          <h3 className="subsection-title">Day-ahead price DE/LU, EUR/MWh</h3>
          <Chart
            build={buildPrice}
            label={`Line chart of the hourly day-ahead electricity price for Germany and Luxembourg in EUR/MWh, ${range}`}
            className="chart-price"
            dataLabel="Price table"
          >
            <PriceTable stamps={priced.stamps} values={prices} />
          </Chart>
          <PriceStats stamps={priced.stamps} values={prices} dueDay={dueDay} />
          <p className="note">{priceNote}</p>
        </>
      ) : null}
    </>
  )
}

/**
 * The price chart runs on the hours of generation and price together, up to the end of
 * `today`, so the prices published for the rest of the day show as well.
 */
function priceGrid(actuals: Actuals, genKeys: readonly string[], today: string): Aligned {
  const all = alignSeries(actuals, [...genKeys, PRICE_KEY])
  const keep = all.stamps.map((ts) => localDay(ts) <= today)
  const pick = <T,>(list: readonly T[]) => list.filter((_, i) => keep[i])
  return {
    stamps: pick(all.stamps),
    values: Object.fromEntries(Object.entries(all.values).map(([key, row]) => [key, pick(row)])),
  }
}

/** `today` is the local day of the status response, so the range ignores the browser clock. */
export function ContextSection({ today, priceNote }: { today: string; priceNote: string }) {
  const start = addDays(today, -(DAYS - 1))
  // Whether a missing price is overdue depends on when the data were loaded: the snapshot
  // time, or the response time if the status names no snapshot. The query is shared with
  // the page, so this needs no request of its own.
  const status = useStatus()
  const reference = status.data?.snapshot_created_at ?? status.data?.generated_at
  const dueDay = reference === undefined ? null : newestDueDay(reference)
  const catalog = useSeries(true)
  const { genKeys, showPrice } = useMemo(() => {
    const available = new Set(catalog.data?.series.map((s) => s.key) ?? [])
    return {
      genKeys: GENERATION.map((g) => g.key).filter((key) => available.has(key)),
      showPrice: available.has(PRICE_KEY),
    }
  }, [catalog.data])
  const keys = showPrice ? [...genKeys, PRICE_KEY] : genKeys
  const actuals = useActuals(keys, start, today, catalog.isSuccess)
  const aligned = useMemo(
    () => (actuals.data ? alignSeries(actuals.data, genKeys) : undefined),
    [actuals.data, genKeys],
  )
  const priced = useMemo(
    () => (actuals.data ? priceGrid(actuals.data, genKeys, today) : undefined),
    [actuals.data, genKeys, today],
  )

  let body
  if (catalog.isError) {
    body = (
      <QueryProblem
        what="the series list"
        error={catalog.error}
        onRetry={() => void catalog.refetch()}
      />
    )
  } else if (actuals.isError) {
    body = (
      <QueryProblem
        what="the generation data"
        error={actuals.error}
        onRetry={() => void actuals.refetch()}
      />
    )
  } else if (catalog.data && aligned && priced) {
    body = (
      <ContextCharts
        catalog={catalog.data}
        aligned={aligned}
        priced={priced}
        showPrice={showPrice}
        priceNote={priceNote}
        dueDay={dueDay}
        start={start}
        end={today}
      />
    )
  } else if (catalog.isSuccess && genKeys.length === 0) {
    body = <p className="notice">This deployment serves no generation series.</p>
  } else {
    body = <Loading what="generation data" reserve="context" />
  }

  return (
    <section className="section" aria-labelledby="context-title">
      <h2 id="context-title" className="section-title">
        {aligned && coversFullDays(aligned.stamps, start, today)
          ? 'Context: the last seven days'
          : 'Context: the past week'}
      </h2>
      {/* Without its data the section shows only the failure, like the others. */}
      {catalog.isError || actuals.isError ? null : (
        <p className="prose">
          Hourly electricity generation in Germany by source
          {showPrice
            ? ', and the day-ahead wholesale price for every hour SMARD has published up to the end of today'
            : ''}
          , from {dayPlain(start)} to {dayPlain(today)}.
        </p>
      )}
      {body}
    </section>
  )
}
