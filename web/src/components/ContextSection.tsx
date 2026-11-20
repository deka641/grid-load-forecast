import { useCallback, useMemo } from 'react'

import type { SeriesCatalog } from '../api/client'
import { useActuals, useSeries } from '../api/queries'
import { Chart } from '../charts/LazyChart'
import { mixOption, priceOption } from '../charts/options'
import type { TimeSeries } from '../charts/options'
import { GENERATION, generationColor } from '../charts/palette'
import type { Palette } from '../charts/palette'
import { NOT_AVAILABLE } from '../lib/format'
import { PRICE_KEY, alignSeries, meanOf, stats } from '../lib/series'
import type { Aligned } from '../lib/series'
import { coversFullDays } from '../lib/days'
import { addDays, dayPlain, dayShort, localDay, offsetLabel, wallClock } from '../lib/time'
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

function PriceStats({ values }: { values: readonly (number | null)[] }) {
  const s = stats(values)
  if (s === null) {
    return <p className="chart-stats muted">No price data in this period.</p>
  }
  return (
    <p className="chart-stats">
      Hourly price from {s.min.toFixed(2)} to {s.max.toFixed(2)} EUR/MWh, mean {s.mean.toFixed(2)}{' '}
      EUR/MWh, negative in {String(s.negativeHours)} of {String(s.hours)} hours.
    </p>
  )
}

function ContextCharts({
  catalog,
  aligned,
  showPrice,
  priceNote,
  start,
  end,
}: {
  catalog: SeriesCatalog
  aligned: Aligned
  showPrice: boolean
  priceNote: string
  start: string
  end: string
}) {
  const series = useMemo(() => generationSeries(catalog, aligned), [catalog, aligned])
  const prices = useMemo(() => aligned.values[PRICE_KEY] ?? [], [aligned])
  const buildMix = useCallback(
    (palette: Palette) =>
      mixOption(aligned.stamps, series, palette, (key) => generationColor(palette, key)),
    [aligned, series],
  )
  const buildPrice = useCallback(
    (palette: Palette) => priceOption(aligned.stamps, prices, palette),
    [aligned, prices],
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
          >
            <PriceTable stamps={aligned.stamps} values={prices} />
          </Chart>
          <PriceStats values={prices} />
          <p className="note">{priceNote}</p>
        </>
      ) : null}
    </>
  )
}

/** `today` is the local day of the status response, so the range ignores the browser clock. */
export function ContextSection({ today, priceNote }: { today: string; priceNote: string }) {
  const start = addDays(today, -(DAYS - 1))
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
  } else if (catalog.data && aligned) {
    body = (
      <ContextCharts
        catalog={catalog.data}
        aligned={aligned}
        showPrice={showPrice}
        priceNote={priceNote}
        start={start}
        end={today}
      />
    )
  } else if (catalog.isSuccess && genKeys.length === 0) {
    body = <p className="notice">This deployment serves no generation series.</p>
  } else {
    body = <Loading what="generation data" />
  }

  return (
    <section className="section" aria-labelledby="context-title">
      <h2 id="context-title" className="section-title">
        {aligned && coversFullDays(aligned.stamps, start, today)
          ? 'Context: the last seven days'
          : 'Context: the past week'}
      </h2>
      <p className="prose">
        Hourly electricity generation in Germany by source
        {showPrice ? ', and the day-ahead wholesale price for the same hours' : ''}, from{' '}
        {dayPlain(start)} to {dayPlain(today)}.
      </p>
      {body}
    </section>
  )
}
