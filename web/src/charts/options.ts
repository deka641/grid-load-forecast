import type { BacktestMetrics, ForecastPoint } from '../api/client'
import { MODELS, mapeByHour } from '../lib/metrics'
import type { ForecastKey } from '../lib/metrics'
import { NOT_AVAILABLE } from '../lib/format'
import {
  addDays,
  berlinParts,
  dayShort,
  hourLabels,
  localDay,
  offsetChange,
  offsetLabel,
  wallClock,
} from '../lib/time'
import type { EChartsCoreOption } from './echarts'
import type { Palette } from './palette'

/** How a chart is drawn beyond its data: its width class and whether it may animate. */
export interface ChartLayout {
  /** A narrow chart, about phone width, which needs sparser ticks and other label places. */
  compact: boolean
  /** The user allows motion (prefers-reduced-motion is not set). */
  motion: boolean
  /** Draw the lines in; only the first render of a chart with motion allowed. */
  animate: boolean
}

export const DEFAULT_LAYOUT: ChartLayout = { compact: false, motion: false, animate: false }

/** Charts narrower than this many pixels use the compact layout. */
export const COMPACT_WIDTH = 520

export type LinePattern = 'solid' | 'dashed' | 'dashdot'

export interface LineStyleSpec {
  color: (palette: Palette) => string
  width: number
  pattern: LinePattern
}

/** Line styles shared by every forecast chart and its legend. */
export const LINE_STYLES: Record<ForecastKey | 'actual', LineStyleSpec> = {
  actual: { color: (p) => p.actual, width: 3, pattern: 'solid' },
  lightgbm: { color: (p) => p.model, width: 2, pattern: 'solid' },
  baseline_week: { color: (p) => p.baseline, width: 2, pattern: 'dashed' },
  tso: { color: (p) => p.tso, width: 2, pattern: 'dashdot' },
}

function dash(pattern: LinePattern): 'solid' | number[] {
  if (pattern === 'dashed') {
    return [6, 4]
  }
  if (pattern === 'dashdot') {
    return [8, 3, 2, 3]
  }
  return 'solid'
}

export function escapeHtml(text: string): string {
  return text
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
}

interface TooltipRow {
  label: string
  value: string
  swatch: string
}

/**
 * Tooltip markup uses classes only. Inline style attributes in innerHTML would be
 * blocked by the Content-Security-Policy (style-src 'self').
 */
export function tooltipHtml(title: string, rows: readonly TooltipRow[]): string {
  const body = rows
    .map(
      (row) =>
        `<tr><th scope="row"><span class="tt-swatch tt-${row.swatch}"></span>${escapeHtml(row.label)}</th>` +
        `<td>${escapeHtml(row.value)}</td></tr>`,
    )
    .join('')
  return `<div class="tt-title">${escapeHtml(title)}</div><table class="tt-table">${body}</table>`
}

/** ECharts passes one param object per series for an axis tooltip. */
interface AxisParam {
  dataIndex: number
}

export function firstIndex(params: unknown): number {
  const list = (Array.isArray(params) ? params : [params]) as AxisParam[]
  return list[0]?.dataIndex ?? 0
}

function base(palette: Palette, layout: ChartLayout = DEFAULT_LAYOUT): EChartsCoreOption {
  return {
    animation: false,
    textStyle: { fontFamily: palette.fontFamily, color: palette.muted, fontSize: 12 },
    grid: { left: 52, right: 16, top: 16, bottom: 28, containLabel: false },
    tooltip: {
      trigger: 'axis',
      renderMode: 'html',
      className: 'chart-tooltip',
      // Set off from the chart surface by its own background, a visible border and a shadow.
      backgroundColor: palette.tooltipBg,
      borderColor: palette.tooltipBorder,
      borderWidth: 1,
      padding: [8, 10],
      extraCssText: 'box-shadow: 0 4px 14px rgba(0, 0, 0, 0.28); border-radius: 4px;',
      transitionDuration: layout.motion ? 0.2 : 0,
      textStyle: { color: palette.ink, fontFamily: palette.fontFamily, fontSize: 13 },
      axisPointer: { type: 'line', lineStyle: { color: palette.muted, width: 1 } },
      confine: true,
    },
  }
}

function valueAxis(palette: Palette, unit: string, extra: Record<string, unknown> = {}) {
  return {
    type: 'value',
    name: unit,
    nameLocation: 'end',
    nameGap: 8,
    nameTextStyle: { color: palette.muted, align: 'right', padding: [0, 6, 0, 0] },
    scale: true,
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: palette.muted },
    splitLine: { lineStyle: { color: palette.grid, width: 1 } },
    ...extra,
  }
}

/** Categories are indexes, so repeated labels such as the doubled 02:00 stay distinct. */
function categoryAxis(palette: Palette, labels: readonly string[], shown: readonly boolean[]) {
  return {
    type: 'category',
    boundaryGap: false,
    data: labels.map((_, i) => String(i)),
    axisLine: { lineStyle: { color: palette.muted } },
    axisTick: { show: false },
    axisLabel: {
      color: palette.muted,
      interval: (index: number) => shown[index] === true,
      formatter: (value: string) => labels[Number(value)] ?? '',
      hideOverlap: true,
    },
    splitLine: { show: false },
  }
}

function lineSeries(
  name: string,
  data: (number | null)[],
  spec: LineStyleSpec,
  palette: Palette,
  z: number,
) {
  return {
    type: 'line',
    name,
    data,
    showSymbol: false,
    symbol: 'none',
    connectNulls: false,
    z,
    lineStyle: { color: spec.color(palette), width: spec.width, type: dash(spec.pattern) },
    itemStyle: { color: spec.color(palette) },
    emphasis: { disabled: true },
  }
}

const gw = (value: number | null) => (value === null ? null : value / 1000)
const gwText = (value: number | null) =>
  value === null ? NOT_AVAILABLE : `${(value / 1000).toFixed(1)} GW`

export const FORECAST_SERIES: readonly { key: ForecastKey | 'actual'; label: string }[] = [
  { key: 'actual', label: 'Actual' },
  ...MODELS.map((m) => ({ key: m.key, label: m.label })),
]

/**
 * Every third wall-clock hour, or every sixth on a compact chart, where three-hour labels
 * collide on clock-change days. The repeated 02:00 of a 25-hour day is never a tick.
 */
export function forecastTick(ts: string, every = 3): boolean {
  return Number(wallClock(ts).slice(0, 2)) % every === 0
}

/** A dashed vertical line where the clock changes, labelled with the offsets on both sides. */
function clockChangeMark(stamps: readonly string[], palette: Palette) {
  const index = offsetChange(stamps)
  const before = stamps[index - 1]
  const after = stamps[index]
  if (before === undefined || after === undefined) {
    return {}
  }
  return {
    markLine: {
      silent: true,
      symbol: 'none',
      animation: false,
      lineStyle: { color: palette.muted, width: 1, type: [4, 3] },
      // Starts at the line and runs to the right, away from the unit name at the top left.
      label: {
        show: true,
        position: 'end',
        distance: 3,
        align: 'left',
        color: palette.muted,
        fontFamily: palette.fontFamily,
        fontSize: 11,
        formatter: `${offsetLabel(before)} | ${offsetLabel(after)}`,
      },
      data: [{ xAxis: String(index) }],
    },
  }
}

/** Hours of a delivery day are labelled by local wall clock; 02:00 repeats on 25-hour days. */
export function forecastOption(
  points: readonly ForecastPoint[],
  palette: Palette,
  layout: ChartLayout = DEFAULT_LAYOUT,
) {
  const option = base(palette, layout)
  // Draw order: forecasts underneath, the actual on top.
  const z: Record<ForecastKey | 'actual', number> = {
    baseline_week: 1,
    tso: 2,
    lightgbm: 3,
    actual: 4,
  }
  return {
    ...option,
    // The one moment of motion on the page: the lines of the newest forecast draw in.
    animation: layout.animate,
    animationDuration: 700,
    animationEasing: 'cubicOut' as const,
    tooltip: {
      ...(option.tooltip as object),
      formatter: (params: unknown) => {
        const point = points[firstIndex(params)]
        if (!point) {
          return ''
        }
        return tooltipHtml(
          `${wallClock(point.ts)} (${offsetLabel(point.ts)})`,
          // No actual yet: the row is left out, as there is no line to read it from.
          FORECAST_SERIES.filter((s) => s.key !== 'actual' || point.actual !== null).map((s) => ({
            label: s.label,
            value: gwText(point[s.key]),
            swatch: s.key,
          })),
        )
      },
    },
    xAxis: categoryAxis(
      palette,
      hourLabels(points.map((p) => p.ts)),
      points.map((p) => forecastTick(p.ts, layout.compact ? 6 : 3)),
    ),
    yAxis: valueAxis(palette, 'GW'),
    series: FORECAST_SERIES.map((s, i) => ({
      ...lineSeries(
        s.label,
        points.map((p) => gw(p[s.key])),
        LINE_STYLES[s.key],
        palette,
        z[s.key],
      ),
      ...(i === 0
        ? clockChangeMark(
            points.map((p) => p.ts),
            palette,
          )
        : {}),
    })),
  }
}

const HOURS = Array.from({ length: 24 }, (_, h) => h)

export function hourLabel(hour: number): string {
  return `${String(hour).padStart(2, '0')}:00`
}

export function mapeByHourOption(backtest: BacktestMetrics, palette: Palette) {
  const option = base(palette)
  const rows = MODELS.map((m) => ({ ...m, values: mapeByHour(backtest, m.key) }))
  return {
    ...option,
    tooltip: {
      ...(option.tooltip as object),
      formatter: (params: unknown) => {
        const hour = firstIndex(params)
        return tooltipHtml(
          hourLabel(hour),
          rows.map((r) => {
            const value = r.values[hour]
            return {
              label: r.short,
              value:
                value === null || value === undefined ? NOT_AVAILABLE : `${value.toFixed(1)} %`,
              swatch: r.key,
            }
          }),
        )
      },
    },
    xAxis: categoryAxis(
      palette,
      HOURS.map(hourLabel),
      HOURS.map((h) => h % 6 === 0),
    ),
    yAxis: valueAxis(palette, '%', { scale: false, min: 0 }),
    series: rows.map((r, i) => lineSeries(r.short, r.values, LINE_STYLES[r.key], palette, 3 - i)),
  }
}

export interface TimeSeries {
  key: string
  label: string
  values: (number | null)[]
}

function timeAxis(palette: Palette, stamps: readonly string[]) {
  return categoryAxis(
    palette,
    stamps.map((ts) => dayShort(localDay(ts), false)),
    stamps.map((ts) => wallClock(ts) === '00:00'),
  )
}

function stampTitle(ts: string): string {
  return `${dayShort(localDay(ts), false)}, ${wallClock(ts)} (${offsetLabel(ts)})`
}

/** Stacked hourly generation in GW. `series` is ordered bottom to top. */
export function mixOption(
  stamps: readonly string[],
  series: readonly TimeSeries[],
  palette: Palette,
  colorOf: (key: string) => string,
) {
  const option = base(palette)
  return {
    ...option,
    tooltip: {
      ...(option.tooltip as object),
      formatter: (params: unknown) => {
        const index = firstIndex(params)
        const stamp = stamps[index]
        if (stamp === undefined) {
          return ''
        }
        let total = 0
        const partial = series.some((s) => (s.values[index] ?? null) === null)
        const rows = [...series].reverse().map((s) => {
          const value = s.values[index] ?? null
          total += value ?? 0
          return {
            label: s.label,
            value: value === null ? NOT_AVAILABLE : `${value.toFixed(1)} GW`,
            swatch: s.key,
          }
        })
        rows.push({
          label: partial ? 'Total (partial)' : 'Total',
          value: `${total.toFixed(1)} GW`,
          swatch: 'none',
        })
        return tooltipHtml(stampTitle(stamp), rows)
      },
    },
    xAxis: timeAxis(palette, stamps),
    yAxis: valueAxis(palette, 'GW', { scale: false, min: 0 }),
    series: series.map((s) => ({
      type: 'line',
      name: s.label,
      data: s.values,
      stack: 'generation',
      showSymbol: false,
      symbol: 'none',
      // A hairline in the surface colour separates neighbouring bands in both themes.
      lineStyle: { width: 0.75, color: palette.surface },
      areaStyle: { color: colorOf(s.key), opacity: 1 },
      itemStyle: { color: colorOf(s.key) },
      emphasis: { disabled: true },
    })),
  }
}

/** Index of the last value that is not null, or -1. */
export function lastValueIndex(values: readonly (number | null)[]): number {
  return values.findLastIndex((v) => v !== null)
}

/**
 * A price holds for the hour that starts at its timestamp, so a step line needs the
 * point where that hour ends. Where the next price is missing, the value is repeated
 * there for drawing only; the tooltip still reads the published values.
 */
export function stepEnds(values: readonly (number | null)[]): (number | null)[] {
  return values.map((value, i) => value ?? (i > 0 ? (values[i - 1] ?? null) : null))
}

/**
 * Day-ahead prices for day D are published around 13:00 Berlin time on D minus 1. Like the
 * pipeline, which waits six hours before it calls a missing value a source gap, a day counts
 * as due only from 19:00 on the day before. Returns the newest due day at `instant`.
 */
export function newestDueDay(instant: string): string {
  const { day, time } = berlinParts(new Date(Date.parse(instant)))
  return time >= '19:00' ? addDays(day, 1) : day
}

export type PriceGapKind = 'pending' | 'source'

/** A run of hours without a price, as indices of its first and last hour. */
export interface PriceGap {
  kind: PriceGapKind
  from: number
  to: number
}

/**
 * The runs of hours without a price. An hour whose day is later than `dueDay` is not yet
 * published (`pending`); any other missing hour is a gap in the source. Without a due day,
 * the hours after the last price count as pending. A missing last hour alone is left out,
 * because the last price is drawn to the end of its hour and the hour is not visible.
 */
export function priceGaps(
  stamps: readonly string[],
  values: readonly (number | null)[],
  dueDay: string | null = null,
): PriceGap[] {
  const lastPrice = lastValueIndex(values)
  if (lastPrice < 0) {
    return []
  }
  const kindOf = (i: number): PriceGapKind =>
    (dueDay === null ? i > lastPrice : localDay(stamps[i] ?? '') > dueDay) ? 'pending' : 'source'
  const gaps: PriceGap[] = []
  for (let i = 0; i < stamps.length; i += 1) {
    if ((values[i] ?? null) !== null) {
      continue
    }
    const kind = kindOf(i)
    const open = gaps.at(-1)
    if (open?.to === i - 1 && open.kind === kind) {
      open.to = i
    } else {
      gaps.push({ kind, from: i, to: i })
    }
  }
  return gaps.filter((g) => g.from < stamps.length - 1)
}

/** First and last axis index of the shading for a gap: up to where the next price starts. */
function gapArea(gap: PriceGap, count: number): [number, number] {
  return [gap.from, Math.min(gap.to + 1, count - 1)]
}

/**
 * A gap in the source is shaded lighter than hours not yet published. Its label sits inside
 * the top of the span, and only when the span is wide enough to hold it.
 */
function sourceGapStart(
  first: number,
  last: number,
  count: number,
  palette: Palette,
  layout: ChartLayout,
) {
  const share = count > 1 ? (last - first) / (count - 1) : 0
  return {
    xAxis: String(first),
    itemStyle: { color: palette.rule, opacity: 0.25 },
    label: {
      show: share >= (layout.compact ? 0.5 : 0.2),
      position: 'insideTop',
      align: 'center',
      verticalAlign: 'top',
      offset: [0, 0],
      formatter: 'No price published by SMARD',
    },
  }
}

export function priceOption(
  stamps: readonly string[],
  values: (number | null)[],
  palette: Palette,
  layout: ChartLayout = DEFAULT_LAYOUT,
  dueDay: string | null = null,
) {
  const option = base(palette, layout)
  const gaps = priceGaps(stamps, values, dueDay)
  const span = gaps.find((g) => g.kind === 'pending') ?? null
  const unpublished =
    gaps.length > 0
      ? {
          markArea: {
            silent: true,
            itemStyle: { color: palette.rule, opacity: 0.5 },
            // Anchored at the right edge of the plot and growing to the left, so the label
            // stays inside the chart however narrow the shaded span is. On a compact chart
            // the span is only a few pixels wide, so the label moves above the plot, where
            // it cannot cover the prices.
            label: {
              show: true,
              ...(layout.compact
                ? {
                    position: ['100%', 0],
                    align: 'right',
                    verticalAlign: 'bottom',
                    offset: [0, -3],
                  }
                : { position: 'insideTopRight' }),
              distance: 4,
              color: palette.muted,
              backgroundColor: palette.surface,
              padding: [1, 3],
              fontFamily: palette.fontFamily,
              fontSize: 11,
              formatter: 'Not yet published',
            },
            data: gaps.map((gap) => {
              const [first, last] = gapArea(gap, stamps.length)
              return [
                gap.kind === 'pending'
                  ? { xAxis: String(first) }
                  : sourceGapStart(first, last, stamps.length, palette, layout),
                { xAxis: String(last) },
              ]
            }),
          },
        }
      : {}
  // Negative prices: the zero line is drawn darker than the other grid lines, and unlike the
  // price line it is grey.
  const zero = values.some((v) => v !== null && v < 0)
    ? {
        markLine: {
          silent: true,
          symbol: 'none',
          label: { show: false },
          lineStyle: { color: palette.muted, width: 1, type: 'solid' },
          data: [{ yAxis: 0 }],
        },
      }
    : {}
  const grid = option.grid as Record<string, unknown>
  return {
    ...option,
    grid: span !== null && layout.compact ? { ...grid, top: 30 } : grid,
    tooltip: {
      ...(option.tooltip as object),
      formatter: (params: unknown) => {
        const index = firstIndex(params)
        const stamp = stamps[index]
        if (stamp === undefined) {
          return ''
        }
        const value = values[index] ?? null
        return tooltipHtml(stampTitle(stamp), [
          {
            label: 'Day-ahead price',
            value: value === null ? NOT_AVAILABLE : `${value.toFixed(2)} EUR/MWh`,
            swatch: 'price',
          },
        ])
      },
    },
    xAxis: timeAxis(palette, stamps),
    yAxis: valueAxis(palette, 'EUR/MWh', { scale: false, splitNumber: 4 }),
    series: [
      {
        type: 'line',
        name: 'Day-ahead price DE/LU',
        data: stepEnds(values),
        showSymbol: false,
        symbol: 'none',
        // A price holds for the hour that starts at its timestamp.
        step: 'end',
        lineStyle: { color: palette.price, width: 1.5 },
        itemStyle: { color: palette.price },
        emphasis: { disabled: true },
        ...unpublished,
        ...zero,
      },
    ],
  }
}
