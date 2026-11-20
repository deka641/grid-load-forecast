import { backtest, forecast } from '../test/fixtures'
import {
  escapeHtml,
  firstIndex,
  forecastOption,
  hourLabel,
  mapeByHourOption,
  mixOption,
  priceOption,
  tooltipHtml,
} from './options'
import type { Palette } from './palette'

const palette: Palette = {
  ink: '#172131',
  muted: '#5a6577',
  rule: '#d6dce4',
  surface: '#ffffff',
  model: '#1d5fbf',
  baseline: '#8792a3',
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
  it('labels the repeated 02:00 hour twice and shows the offset in the tooltip', () => {
    const day = forecast('2026-10-25')
    const option = built(forecastOption(day.points, palette))
    expect(option.animation).toBe(false)
    expect(option.xAxis.data).toHaveLength(25)
    const labels = option.xAxis.data.map((v) => option.xAxis.axisLabel.formatter(v))
    expect(labels.filter((l) => l === '02:00')).toHaveLength(2)
    // Both 02:00 hours carry a label; 03:00 gives way to them.
    expect(option.xAxis.axisLabel.interval(2)).toBe(true)
    expect(option.xAxis.axisLabel.interval(3)).toBe(true)
    expect(option.xAxis.axisLabel.interval(4)).toBe(false)
    expect(option.xAxis.axisLabel.interval(7)).toBe(true)
    expect(option.tooltip.formatter([{ dataIndex: 2 }])).toContain('02:00 (UTC+2)')
    expect(option.tooltip.formatter({ dataIndex: 3 })).toContain('02:00 (UTC+1)')
    expect(option.tooltip.formatter([{ dataIndex: 99 }])).toBe('')
    expect(option.yAxis.name).toBe('GW')
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
    expect(option.tooltip.formatter([{ dataIndex: 0 }])).toContain('n/a')
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
    expect(option.xAxis.axisLabel.interval(1)).toBe(true)
    expect(option.xAxis.axisLabel.interval(0)).toBe(false)
    expect(option.xAxis.axisLabel.formatter('1')).toBe('Fri 20 Nov')
    const html = option.tooltip.formatter([{ dataIndex: 1 }])
    expect(html).toContain('Fri 20 Nov, 00:00 (UTC+1)')
    expect(html).toContain('Total')
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
