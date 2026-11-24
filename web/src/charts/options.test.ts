import { backtest, forecast } from '../test/fixtures'
import {
  DEFAULT_LAYOUT,
  escapeHtml,
  firstIndex,
  forecastOption,
  hourLabel,
  lastValueIndex,
  mapeByHourOption,
  mixOption,
  newestDueDay,
  priceGaps,
  priceOption,
  roundedGwRange,
  tooltipHtml,
  tooltipPosition,
} from './options'
import type { Palette } from './palette'

const palette: Palette = {
  ink: '#172131',
  muted: '#5a6577',
  rule: '#d6dce4',
  grid: '#e3e7ec',
  surface: '#ffffff',
  tooltipBg: '#ffffff',
  tooltipBorder: '#7d8898',
  model: '#1d5fbf',
  baseline: '#77829a',
  tso: '#0f8476',
  actual: '#172131',
  price: '#172131',
  glow: 'transparent',
  fontFamily: 'Barlow',
  generation: { gen_solar: '#e9c32d' },
}

interface Axis {
  data: string[]
  axisLabel: { interval: (i: number) => boolean; formatter: (v: string) => string }
}
interface Series {
  name: string
  data: (number | null)[]
  step?: string
  lineStyle: { color?: string; type?: unknown; width: number }
  areaStyle?: { color: string }
  z?: number
  markLine?: { data: unknown[]; label: { formatter: string }; lineStyle: { color: string } }
  markArea?: {
    data: {
      xAxis: string
      label?: { formatter: string; show?: boolean }
      itemStyle?: { opacity: number }
    }[][]
    label: { formatter: string }
  }
}
interface Built {
  animation: boolean
  xAxis: Axis
  yAxis: { name: string }
  series: Series[]
  tooltip: { formatter: (params: unknown) => string }
}

const built = (option: unknown) => option as Built

describe('forecastOption', () => {
  it('names the repeated 02:00 hour with its offset and marks the clock change', () => {
    const day = forecast('2026-10-25')
    const option = built(forecastOption(day.points, palette))
    expect(option.animation).toBe(false)
    expect(option.xAxis.data).toHaveLength(25)
    const labels = option.xAxis.data.map((v) => option.xAxis.axisLabel.formatter(v))
    expect(labels.slice(1, 5)).toEqual(['01:00', '02:00 (UTC+2)', '02:00 (UTC+1)', '03:00'])
    // Ticks keep the three-hour rhythm of the wall clock; neither 02:00 is one.
    expect(option.xAxis.axisLabel.interval(0)).toBe(true)
    expect(option.xAxis.axisLabel.interval(2)).toBe(false)
    expect(option.xAxis.axisLabel.interval(3)).toBe(false)
    expect(option.xAxis.axisLabel.interval(4)).toBe(true)
    expect(option.xAxis.axisLabel.interval(7)).toBe(true)
    expect(option.tooltip.formatter([{ dataIndex: 2 }])).toContain('02:00 (UTC+2)')
    expect(option.tooltip.formatter({ dataIndex: 3 })).toContain('02:00 (UTC+1)')
    expect(option.tooltip.formatter([{ dataIndex: 99 }])).toBe('')
    expect(option.yAxis.name).toBe('GW')
    const mark = option.series[0]?.markLine
    expect(mark?.data).toEqual([{ xAxis: '3' }])
    expect(mark?.label.formatter).toBe('UTC+2 | UTC+1')
    expect(option.series.slice(1).every((s) => s.markLine === undefined)).toBe(true)
  })

  it('marks no clock change on a normal day', () => {
    const option = built(forecastOption(forecast('2026-11-20').points, palette))
    expect(option.series.every((s) => s.markLine === undefined)).toBe(true)
    expect(option.xAxis.axisLabel.formatter('2')).toBe('02:00')
  })

  it('converts to GW and styles each line', () => {
    const day = forecast('2026-03-29', false)
    const option = built(forecastOption(day.points, palette))
    expect(option.xAxis.data).toHaveLength(23)
    expect(option.xAxis.axisLabel.interval(2)).toBe(true)
    expect(option.xAxis.axisLabel.interval(1)).toBe(false)
    const [actual, model, baseline, tso] = option.series
    expect(actual?.data[0]).toBeNull()
    expect(model?.data[0]).toBeCloseTo(51)
    expect(actual?.lineStyle.width).toBe(3)
    expect(actual?.z).toBe(4)
    expect(baseline?.lineStyle.type).toEqual([6, 4])
    expect(tso?.lineStyle.type).toEqual([8, 3, 2, 3])
    expect(model?.lineStyle.color).toBe(palette.model)
  })

  it('leaves the actual out of the tooltip until it is published', () => {
    const pending = built(forecastOption(forecast('2026-11-21', false).points, palette))
    const html = pending.tooltip.formatter([{ dataIndex: 0 }])
    expect(html).not.toContain('Actual')
    expect(html).not.toContain('tt-actual')
    expect(html).toContain('LightGBM')

    const points = forecast('2026-11-20').points.map((p, i) => ({
      ...p,
      actual: i < 2 ? p.actual : null,
      tso: null,
    }))
    const partial = built(forecastOption(points, palette))
    const published = partial.tooltip.formatter([{ dataIndex: 0 }])
    expect(published).toContain('tt-actual')
    // Forecasts without a value still show n/a.
    expect(published).toContain('n/a')
    expect(partial.tooltip.formatter([{ dataIndex: 5 }])).not.toContain('tt-actual')
  })
})

describe('mapeByHourOption', () => {
  it('plots 24 hours with n/a for missing models', () => {
    const option = built(mapeByHourOption(backtest(), palette))
    expect(option.xAxis.data).toHaveLength(24)
    expect(option.xAxis.axisLabel.formatter('6')).toBe('06:00')
    expect(option.xAxis.axisLabel.interval(6)).toBe(true)
    const html = option.tooltip.formatter([{ dataIndex: 10 }])
    expect(html).toContain('3.4 %')
    expect(html).toContain('n/a')
    expect(hourLabel(23)).toBe('23:00')
  })
})

describe('mixOption and priceOption', () => {
  const stamps = ['2026-11-19T23:00:00+01:00', '2026-11-20T00:00:00+01:00']

  it('stacks generation and totals the tooltip', () => {
    const series = [
      { key: 'gen_solar', label: 'Photovoltaics', values: [0, 1.5] },
      { key: 'gen_x', label: 'Other', values: [2, null] },
    ]
    const option = built(mixOption(stamps, series, palette, (k) => palette.generation[k] ?? 'grey'))
    expect(option.series[0]?.areaStyle?.color).toBe('#e9c32d')
    // Hairlines in the surface colour separate the bands.
    for (const s of option.series) {
      expect(s.lineStyle).toEqual({ width: 0.75, color: palette.surface })
    }
    expect(option.xAxis.axisLabel.interval(1)).toBe(true)
    expect(option.xAxis.axisLabel.interval(0)).toBe(false)
    expect(option.xAxis.axisLabel.formatter('1')).toBe('Fri 20 Nov')
    const html = option.tooltip.formatter([{ dataIndex: 1 }])
    expect(html).toContain('Fri 20 Nov, 00:00 (UTC+1)')
    expect(html).toContain('Total (partial)')
    expect(option.tooltip.formatter([{ dataIndex: 0 }])).toContain('>Total<')
    expect(html).toContain('1.5 GW')
    expect(html).toContain('n/a')
    expect(option.tooltip.formatter([{ dataIndex: 5 }])).toBe('')
  })

  it('draws the price as a step line in EUR/MWh', () => {
    const option = built(priceOption(stamps, [-5.5, null], palette))
    expect(option.yAxis.name).toBe('EUR/MWh')
    expect(option.tooltip.formatter([{ dataIndex: 0 }])).toContain('-5.50 EUR/MWh')
    expect(option.tooltip.formatter([{ dataIndex: 1 }])).toContain('n/a')
    expect(option.tooltip.formatter([{ dataIndex: 2 }])).toBe('')
  })

  it('draws the zero line strongly only when a price is negative', () => {
    const negative = built(priceOption(stamps, [-5.5, 10], palette))
    expect(negative.series[0]?.markLine?.data).toEqual([{ yAxis: 0 }])
    expect(negative.series[0]?.markLine?.lineStyle.color).toBe(palette.muted)
    const positive = built(priceOption(stamps, [0, 10], palette))
    expect(positive.series[0]?.markLine).toBeUndefined()
  })

  it('shades the hours after the last published price', () => {
    const hours = [
      '2026-11-20T21:00:00+01:00',
      '2026-11-20T22:00:00+01:00',
      '2026-11-20T23:00:00+01:00',
      '2026-11-21T00:00:00+01:00',
      '2026-11-21T01:00:00+01:00',
    ]
    const early = built(priceOption(hours, [50, 60, null, null, null], palette))
    expect(early.series[0]?.markArea?.data).toEqual([[{ xAxis: '2' }, { xAxis: '4' }]])
    expect(early.series[0]?.markArea?.label.formatter).toBe('Not yet published')
    // The step line already draws the last price up to the next hour, where the shading
    // starts, so no value is drawn for the first hour without a price.
    expect(early.series[0]?.data).toEqual([50, 60, null, null, null])
    expect(early.tooltip.formatter([{ dataIndex: 2 }])).toContain('n/a')
    expect(priceGaps(hours, [50, 60, null, null, null])).toEqual([
      { kind: 'pending', from: 2, to: 4 },
    ])
    // Only the last hour missing, complete or empty: nothing to shade.
    for (const values of [
      [1, 2, 3, 4, null],
      [1, 2, 3, 4, 5],
      [null, null, null, null, null],
    ]) {
      expect(built(priceOption(hours, values, palette)).series[0]?.markArea).toBeUndefined()
    }
    // The missing last hour is still a gap; only the shading leaves it out.
    expect(priceGaps(hours, [1, 2, 3, 4, null])).toEqual([{ kind: 'pending', from: 4, to: 4 }])
    expect(priceGaps(hours, [1, 2, 3, 4, 5])).toEqual([])
    expect(priceGaps(hours, [null, null, null, null, null])).toEqual([])
  })

  it('draws each price up to the next hour and no further', () => {
    const hours = [
      '2026-11-20T20:00:00+01:00',
      '2026-11-20T21:00:00+01:00',
      '2026-11-20T22:00:00+01:00',
      '2026-11-20T23:00:00+01:00',
      '2026-11-21T00:00:00+01:00',
    ]
    // With step 'end', each point is drawn to the next axis position even when the next
    // value is missing, so the series holds the published prices unchanged: after the last
    // price, inside a gap, before the last hour, and when the last hour has a price.
    for (const values of [
      [1, 2, null, null, null],
      [1, null, null, 2, null],
      [1, 2, 3, 4, null],
      [1, 2, 3, 4, 5],
      [null, 2, 3, 4, 5],
    ]) {
      const series = built(priceOption(hours, values, palette)).series[0]
      expect(series?.step).toBe('end')
      expect(series?.data).toEqual(values)
    }
    expect(lastValueIndex([1, null, 2, null])).toBe(2)
    expect(lastValueIndex([null])).toBe(-1)
  })
})

describe('price gaps', () => {
  const pad = (n: number) => String(n).padStart(2, '0')
  const hoursOf = (day: string, from: number, to: number, offset = '+01:00') =>
    Array.from({ length: to - from + 1 }, (_, i) => `${day}T${pad(from + i)}:00:00${offset}`)
  // 20 Nov 20:00 to 21 Nov 03:00.
  const hours = [...hoursOf('2026-11-20', 20, 23), ...hoursOf('2026-11-21', 0, 3)]
  // Start, end and label text of each shaded span.
  const areas = (area: Series['markArea']) =>
    (area?.data ?? []).map(([start, end]) => [
      start?.xAxis,
      end?.xAxis,
      start?.label?.formatter ?? area?.label.formatter,
    ])

  it('takes a day as due from 19:00 Berlin time on the day before', () => {
    expect(newestDueDay('2026-11-20T18:59:00+01:00')).toBe('2026-11-20')
    expect(newestDueDay('2026-11-20T19:00:00+01:00')).toBe('2026-11-21')
    // The instant decides, not the offset it is written with.
    expect(newestDueDay('2026-11-20T17:59:00Z')).toBe('2026-11-20')
    expect(newestDueDay('2026-11-20T18:00:00Z')).toBe('2026-11-21')
    expect(newestDueDay('2026-07-01T17:00:00Z')).toBe('2026-07-02')
    expect(newestDueDay('2026-12-31T23:30:00+01:00')).toBe('2027-01-01')
    expect(newestDueDay('not a time')).toBeNull()
    expect(newestDueDay('')).toBeNull()
  })

  it('labels a missing run inside the range as a source gap', () => {
    const values = [1, 2, null, null, null, 6, 7, 8]
    expect(priceGaps(hours, values, '2026-11-21')).toEqual([{ kind: 'source', from: 2, to: 4 }])
    const option = built(priceOption(hours, values, palette, undefined, '2026-11-21'))
    const area = option.series[0]?.markArea
    // The shading ends where the next price starts.
    expect(areas(area)).toEqual([['2', '5', 'No price published by SMARD']])
    // Lighter than the shading of hours not yet published.
    expect(area?.data[0]?.[0]?.itemStyle?.opacity).toBeLessThan(0.5)
    // Labelled in the chart where the span can hold the text; the summary names it always.
    expect(area?.data[0]?.[0]?.label?.show).toBe(true)
    const compact = built(
      priceOption(hours, values, palette, { ...DEFAULT_LAYOUT, compact: true }, '2026-11-21'),
    ).series[0]?.markArea
    expect(compact?.data[0]?.[0]?.label?.show).toBe(false)
  })

  it('labels a missing end of the range as a source gap once its day is due', () => {
    const values = [1, 2, 3, null, null, null, null, null]
    expect(priceGaps(hours, values, '2026-11-21')).toEqual([{ kind: 'source', from: 3, to: 7 }])
    const area = built(priceOption(hours, values, palette, undefined, '2026-11-21')).series[0]
      ?.markArea
    expect(areas(area)).toEqual([['3', '7', 'No price published by SMARD']])
  })

  it('keeps hours of a day not yet due as not yet published', () => {
    const values = [1, 2, null, null, null, null, null, null]
    // 20 Nov is due, 21 Nov is not: the run splits at midnight.
    expect(priceGaps(hours, values, '2026-11-20')).toEqual([
      { kind: 'source', from: 2, to: 3 },
      { kind: 'pending', from: 4, to: 7 },
    ])
    const area = built(priceOption(hours, values, palette, undefined, '2026-11-20')).series[0]
      ?.markArea
    expect(area?.label.formatter).toBe('Not yet published')
    expect(areas(area)).toEqual([
      ['2', '4', 'No price published by SMARD'],
      ['4', '7', 'Not yet published'],
    ])
    expect(area?.data[1]).toEqual([{ xAxis: '4' }, { xAxis: '7' }])
  })

  it('finds gaps at the start and does not shade a missing last hour', () => {
    const values = [null, null, 3, 4, 5, 6, 7, null]
    expect(priceGaps(hours, values, '2026-11-21')).toEqual([
      { kind: 'source', from: 0, to: 1 },
      { kind: 'source', from: 7, to: 7 },
    ])
    const area = built(priceOption(hours, values, palette, undefined, '2026-11-21')).series[0]
      ?.markArea
    expect(areas(area)).toEqual([['0', '2', 'No price published by SMARD']])
  })

  it('counts the hours of a gap over the 25-hour and the 23-hour day', () => {
    const autumn = [
      '2026-10-24T23:00:00+02:00',
      ...hoursOf('2026-10-25', 0, 2, '+02:00'),
      ...hoursOf('2026-10-25', 2, 23),
      '2026-10-26T00:00:00+01:00',
    ]
    expect(autumn).toHaveLength(27)
    const gapsAutumn = priceGaps(autumn, [1, ...new Array<null>(25).fill(null), 2], '2026-10-26')
    expect(gapsAutumn).toEqual([{ kind: 'source', from: 1, to: 25 }])
    const spring = [
      '2026-03-28T23:00:00+01:00',
      ...hoursOf('2026-03-29', 0, 1),
      ...hoursOf('2026-03-29', 3, 23, '+02:00'),
      '2026-03-30T00:00:00+02:00',
    ]
    expect(spring).toHaveLength(25)
    expect(priceGaps(spring, [1, ...new Array<null>(23).fill(null), 2], '2026-03-30')).toEqual([
      { kind: 'source', from: 1, to: 23 },
    ])
    // Over both clock changes the last price is drawn to the next hour only, and the
    // shading starts there.
    for (const day of [autumn, spring]) {
      const values = day.map((_, i) => (i < day.length - 3 ? i : null))
      const option = built(priceOption(day, values, palette))
      expect(option.series[0]?.data).toEqual(values)
      expect(option.series[0]?.data[day.length - 3]).toBeNull()
      expect(option.series[0]?.markArea?.data[0]?.[0]?.xAxis).toBe(String(day.length - 3))
    }
  })
})

describe('tooltip markup', () => {
  it('escapes text and uses classes, never inline styles', () => {
    expect(escapeHtml('<a href="x">&</a>')).toBe('&lt;a href=&quot;x&quot;&gt;&amp;&lt;/a&gt;')
    const html = tooltipHtml('T<1>', [{ label: 'A&B', value: '1 GW', swatch: 'tso' }])
    expect(html).toContain('T&lt;1&gt;')
    expect(html).toContain('tt-tso')
    expect(html).not.toContain('style=')
    expect(firstIndex([])).toBe(0)
  })
})

describe('forecast chart finish', () => {
  interface Finish {
    yAxis: { min?: number; max?: number; interval?: number }
    xAxis: { axisPointer: { label: { formatter: (p: { value: string }) => string } } }
    series: {
      name: string
      animationDelay: number
      markPoint?: { silent: boolean; data: { coord: [string, number] }[] }
    }[]
  }

  it('marks the peak of the model line that the headline names, and nothing else', () => {
    const points = forecast('2026-11-20').points
    const option = forecastOption(points, palette) as unknown as Finish
    const marked = option.series.filter((s) => s.markPoint !== undefined)
    expect(marked.map((s) => s.name)).toEqual(['LightGBM model'])
    const peak = Math.max(...points.map((p) => p.lightgbm ?? -Infinity))
    const index = points.findIndex((p) => p.lightgbm === peak)
    expect(marked[0]?.markPoint?.data).toEqual([{ coord: [String(index), peak / 1000] }])
    expect(marked[0]?.markPoint?.silent).toBe(true)
  })

  it('lets only the model line and its peak dot glow, and only where the theme has a glow', () => {
    interface Glow {
      series: {
        name: string
        lineStyle: { shadowColor?: string; shadowBlur?: number }
        markPoint?: { itemStyle: { shadowColor?: string } }
      }[]
    }
    const points = forecast('2026-11-20').points
    const light = forecastOption(points, palette) as unknown as Glow
    expect(light.series.every((s) => s.lineStyle.shadowColor === undefined)).toBe(true)
    const glow = 'rgba(110, 164, 242, 0.55)'
    const dark = forecastOption(points, { ...palette, glow }) as unknown as Glow
    const glowing = dark.series.filter((s) => s.lineStyle.shadowColor === glow)
    expect(glowing.map((s) => s.name)).toEqual(['LightGBM model'])
    expect(glowing[0]?.markPoint?.itemStyle.shadowColor).toBe(glow)
  })

  it('rounds the value axis to whole 5 GW steps around all lines', () => {
    const points = forecast('2026-11-20').points
    const option = forecastOption(points, palette) as unknown as Finish
    const values = points.flatMap((p) =>
      [p.actual, p.lightgbm, p.baseline_week, p.tso].filter((v): v is number => v !== null),
    )
    const { min, max, interval } = option.yAxis
    expect(interval).toBe(5)
    expect((min ?? NaN) % 5).toBe(0)
    expect((max ?? NaN) % 5).toBe(0)
    expect(min).toBeLessThanOrEqual(Math.min(...values) / 1000)
    expect(max).toBeGreaterThanOrEqual(Math.max(...values) / 1000)
  })

  it('draws the lines in one after another, the actual last', () => {
    const option = forecastOption(forecast('2026-11-20').points, palette) as unknown as Finish
    const delay = Object.fromEntries(option.series.map((s) => [s.name, s.animationDelay]))
    expect(delay['Baseline (same hour last week)']).toBe(0)
    expect(delay.Actual).toBeGreaterThan(delay['LightGBM model'] ?? Infinity)
  })

  it('names the hovered hour in the axis pointer badge, not its index', () => {
    const option = forecastOption(forecast('2026-11-20').points, palette) as unknown as Finish
    expect(option.xAxis.axisPointer.label.formatter({ value: '17' })).toBe('17:00')
  })
})

describe('tooltipPosition', () => {
  const size = {
    contentSize: [200, 120] as [number, number],
    viewSize: [1000, 380] as [number, number],
  }

  it('sits at the top, on the side away from the cursor', () => {
    expect(tooltipPosition([300, 200], size)).toEqual([316, 8])
    expect(tooltipPosition([700, 200], size)).toEqual([484, 8])
  })

  it('stays inside the chart', () => {
    const narrow = {
      contentSize: [200, 120] as [number, number],
      viewSize: [300, 300] as [number, number],
    }
    expect(tooltipPosition([140, 50], narrow)).toEqual([100, 8])
    expect(tooltipPosition([160, 50], narrow)).toEqual([0, 8])
  })
})

describe('forecast axis and peak edge cases', () => {
  interface Marked {
    series: { name: string; markPoint?: { data: { coord: [string, number] }[] } }[]
  }
  const peakCoord = (points: ReturnType<typeof forecast>['points']) =>
    (forecastOption(points, palette) as unknown as Marked).series.find((s) => s.markPoint)
      ?.markPoint?.data[0]?.coord

  it('drops the fixed 5 GW step when it would give more than 20 ticks', () => {
    const points = forecast('2026-11-20').points.map((p, i) => ({
      ...p,
      lightgbm: i === 5 ? 160_000 : p.lightgbm,
    }))
    const range = roundedGwRange(points)
    expect(range?.max).toBe(160)
    expect(range?.interval).toBeUndefined()
    expect(roundedGwRange(forecast('2026-11-20').points)?.interval).toBe(5)
  })

  it('marks the first of two equal peak hours, as the headline names it', () => {
    const points = forecast('2026-11-20').points.map((p, i) => ({
      ...p,
      lightgbm: i === 8 || i === 15 ? 90_000 : p.lightgbm,
    }))
    expect(peakCoord(points)).toEqual(['8', 90])
  })

  it('marks the repeated 02:00 hour of the 25-hour day by its own index', () => {
    const points = forecast('2026-10-25').points.map((p, i) => ({
      ...p,
      lightgbm: i === 3 ? 99_900 : p.lightgbm,
    }))
    expect(points).toHaveLength(25)
    expect(peakCoord(points)).toEqual(['3', 99.9])
  })
})
