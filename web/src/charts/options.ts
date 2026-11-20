import type { BacktestMetrics, ForecastPoint } from '../api/client'
import { MODELS, mapeByHour } from '../lib/metrics'
import type { ForecastKey } from '../lib/metrics'
import { NOT_AVAILABLE } from '../lib/format'
import { dayShort, localDay, offsetLabel, wallClock } from '../lib/time'
import type { EChartsCoreOption } from './echarts'
import type { Palette } from './palette'

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

function base(palette: Palette): EChartsCoreOption {
  return {
    animation: false,
    textStyle: { fontFamily: palette.fontFamily, color: palette.muted, fontSize: 12 },
    grid: { left: 52, right: 16, top: 16, bottom: 28, containLabel: false },
    tooltip: {
      trigger: 'axis',
      renderMode: 'html',
      className: 'chart-tooltip',
      backgroundColor: palette.surface,
      borderColor: palette.rule,
      borderWidth: 1,
      padding: [8, 10],
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
    splitLine: { lineStyle: { color: palette.rule, width: 1 } },
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

/** Hours of a delivery day are labelled by local wall clock; 02:00 repeats on 25-hour days. */
export function forecastOption(points: readonly ForecastPoint[], palette: Palette) {
  const option = base(palette)
  // Draw order: forecasts underneath, the actual on top.
  const z: Record<ForecastKey | 'actual', number> = {
    baseline_week: 1,
    tso: 2,
    lightgbm: 3,
    actual: 4,
  }
  return {
    ...option,
    tooltip: {
      ...(option.tooltip as object),
      formatter: (params: unknown) => {
        const point = points[firstIndex(params)]
        if (!point) {
          return ''
        }
        return tooltipHtml(
          `${wallClock(point.ts)} (${offsetLabel(point.ts)})`,
          FORECAST_SERIES.map((s) => ({
            label: s.label,
            value: gwText(point[s.key]),
            swatch: s.key,
          })),
        )
      },
    },
    xAxis: categoryAxis(
      palette,
      points.map((p) => wallClock(p.ts)),
      points.map((p) => Number(wallClock(p.ts).slice(0, 2)) % 3 === 0),
    ),
    yAxis: valueAxis(palette, 'GW'),
    series: FORECAST_SERIES.map((s) =>
      lineSeries(
        s.label,
        points.map((p) => gw(p[s.key])),
        LINE_STYLES[s.key],
        palette,
        z[s.key],
      ),
    ),
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
        const rows = [...series].reverse().map((s) => {
          const value = s.values[index] ?? null
          total += value ?? 0
          return {
            label: s.label,
            value: value === null ? NOT_AVAILABLE : `${value.toFixed(1)} GW`,
            swatch: s.key,
          }
        })
        rows.push({ label: 'Total', value: `${total.toFixed(1)} GW`, swatch: 'none' })
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
      lineStyle: { width: 0 },
      areaStyle: { color: colorOf(s.key), opacity: 1 },
      itemStyle: { color: colorOf(s.key) },
      emphasis: { disabled: true },
    })),
  }
}

export function priceOption(
  stamps: readonly string[],
  values: (number | null)[],
  palette: Palette,
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
        data: values,
        showSymbol: false,
        symbol: 'none',
        step: 'start',
        lineStyle: { color: palette.price, width: 1.5 },
        itemStyle: { color: palette.price },
        emphasis: { disabled: true },
      },
    ],
  }
}
