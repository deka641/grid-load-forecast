import { backtest, forecast } from '../test/fixtures'
import {
  escapeHtml,
  firstIndex,
  forecastOption,
  hourLabel,
  lastValueIndex,
  mapeByHourOption,
  mixOption,
  priceOption,
  roundedGwRange,
  stepEnds,
  tooltipHtml,
  tooltipPosition,
  unpublishedSpan,
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
  lineStyle: { color?: string; type?: unknown; width: number }
  areaStyle?: { color: string }
  z?: number
  markLine?: { data: unknown[]; label: { formatter: string }; lineStyle: { color: string } }
  markArea?: { data: unknown[][]; label: { formatter: string } }
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
    // The last price is drawn over its whole hour; the tooltip keeps the gap.
    expect(early.series[0]?.data).toEqual([50, 60, 60, null, null])
    expect(early.tooltip.formatter([{ dataIndex: 2 }])).toContain('n/a')
    expect(unpublishedSpan(hours, [50, 60, null, null, null])).toEqual({
      first: 2,
      last: 4,
      lastPrice: 1,
    })
    // Only the last hour missing, complete or empty: nothing to shade.
    for (const values of [
      [1, 2, 3, 4, null],
      [1, 2, 3, 4, 5],
      [null, null, null, null, null],
    ]) {
      expect(built(priceOption(hours, values, palette)).series[0]?.markArea).toBeUndefined()
      expect(unpublishedSpan(hours, values)).toBeNull()
    }
  })

  it('extends each price to the end of its hour', () => {
    expect(stepEnds([1, null, null, 2, null])).toEqual([1, 1, null, 2, 2])
    expect(stepEnds([null, 3])).toEqual([null, 3])
    expect(lastValueIndex([1, null, 2, null])).toBe(2)
    expect(lastValueIndex([null])).toBe(-1)
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
