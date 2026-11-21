import { backtest, forecast } from '../test/fixtures'
import { forecastOption, mapeByHourOption, priceOption, withAlpha } from './options'
import type { ChartLayout } from './options'
import type { Palette } from './palette'

const palette: Palette = {
  ink: '#e4e9f0',
  muted: '#909cae',
  rule: '#2a3647',
  grid: '#223040',
  surface: '#152030',
  tooltipBg: '#0a1019',
  tooltipBorder: '#64738a',
  model: '#6ea4f2',
  baseline: '#6f7b8f',
  tso: '#2b9c8d',
  actual: '#e4e9f0',
  price: '#e4e9f0',
  fontFamily: 'Barlow',
  generation: {},
}

const wide: ChartLayout = { compact: false, motion: true, animate: true }
const narrow: ChartLayout = { compact: true, motion: false, animate: false }

interface Option {
  animation: boolean
  grid: { top: number }
  xAxis: { data: string[]; axisLabel: { interval: (i: number) => boolean } }
  yAxis: { splitLine: { lineStyle: { color: string } } }
  tooltip: { backgroundColor: string; borderColor: string; transitionDuration: number }
  series: {
    name: string
    areaStyle?: { color: { colorStops: { color: string }[] } }
    markLine?: { label: { align: string } }
    markArea?: { label: { position: unknown; verticalAlign?: string } }
  }[]
}
const as = (option: unknown) => option as Option

function ticks(option: Option): number[] {
  return option.xAxis.data.map(Number).filter((i) => option.xAxis.axisLabel.interval(i))
}

describe('chart layout', () => {
  it('uses six-hour ticks on a compact chart, which keep 00:00 and 06:00 on clock-change days', () => {
    // 25 hours: 00, 01, 02 (UTC+2), 02 (UTC+1), 03, ..., so 06:00 is index 7.
    expect(ticks(as(forecastOption(forecast('2026-10-25').points, palette, narrow)))).toEqual([
      0, 7, 13, 19,
    ])
    // 23 hours: 00, 01, 03, ..., so 06:00 is index 5.
    expect(ticks(as(forecastOption(forecast('2026-03-29').points, palette, narrow)))).toEqual([
      0, 5, 11, 17,
    ])
    expect(ticks(as(forecastOption(forecast('2026-11-20').points, palette, wide)))).toEqual([
      0, 3, 6, 9, 12, 15, 18, 21,
    ])
  })

  it('starts the clock-change label at the line, away from the unit name', () => {
    const option = as(forecastOption(forecast('2026-03-29').points, palette, narrow))
    expect(option.series[0]?.markLine?.label.align).toBe('left')
  })

  it('draws in only when the layout asks for it', () => {
    const points = forecast('2026-11-20').points
    expect(as(forecastOption(points, palette, wide)).animation).toBe(true)
    expect(as(forecastOption(points, palette, narrow)).animation).toBe(false)
    expect(as(forecastOption(points, palette)).animation).toBe(false)
    // The other charts never animate.
    expect(as(mapeByHourOption(backtest(), palette)).animation).toBe(false)
    expect(as(forecastOption(points, palette, wide)).tooltip.transitionDuration).toBe(0.2)
    expect(as(forecastOption(points, palette, narrow)).tooltip.transitionDuration).toBe(0)
  })

  it('fills only under the model line, on calm grid lines, with a lifted tooltip', () => {
    const option = as(forecastOption(forecast('2026-11-20').points, palette, wide))
    const filled = option.series.filter((s) => s.areaStyle !== undefined)
    expect(filled.map((s) => s.name)).toEqual(['LightGBM model'])
    expect(filled[0]?.areaStyle?.color.colorStops.map((c) => c.color)).toEqual([
      'rgba(110, 164, 242, 0.16)',
      'rgba(110, 164, 242, 0)',
    ])
    expect(option.yAxis.splitLine.lineStyle.color).toBe(palette.grid)
    expect(option.tooltip.backgroundColor).toBe(palette.tooltipBg)
    expect(option.tooltip.borderColor).toBe(palette.tooltipBorder)
  })

  it('moves the unpublished label above a compact price chart', () => {
    const hours = [
      '2026-11-20T21:00:00+01:00',
      '2026-11-20T22:00:00+01:00',
      '2026-11-20T23:00:00+01:00',
      '2026-11-21T00:00:00+01:00',
    ]
    const values = [50, null, null, null]
    const compact = as(priceOption(hours, values, palette, narrow))
    expect(compact.series[0]?.markArea?.label.position).toEqual(['100%', 0])
    expect(compact.series[0]?.markArea?.label.verticalAlign).toBe('bottom')
    expect(compact.grid.top).toBeGreaterThan(16)
    const full = as(priceOption(hours, values, palette, wide))
    expect(full.series[0]?.markArea?.label.position).toBe('insideTopRight')
    expect(full.grid.top).toBe(16)
    // Without a shaded span the compact chart keeps the usual top margin.
    expect(as(priceOption(hours, [1, 2, 3, 4], palette, narrow)).grid.top).toBe(16)
  })

  it('adds an opacity only to six-digit hex colours', () => {
    expect(withAlpha('#1d5fbf', 0.5)).toBe('rgba(29, 95, 191, 0.5)')
    expect(withAlpha('rgb(1, 2, 3)', 0.5)).toBe('rgb(1, 2, 3)')
  })
})
