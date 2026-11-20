import { backtest, forecast } from '../test/fixtures'
import { formatGW, formatPercent, formatPrice, toGW } from './format'
import { dayMape, findMetric, forecastPeak, hasActuals, headline, mapeByHour } from './metrics'

describe('format', () => {
  it('converts MWh per hour to GW', () => {
    expect(toGW(68_400)).toBe(68.4)
    expect(formatGW(68_449)).toBe('68.4 GW')
    expect(formatGW(1312, 2)).toBe('1.31 GW')
    expect(formatPercent(2.345)).toBe('2.3 %')
    expect(formatPrice(-5.5)).toBe('-5.50 EUR/MWh')
  })
})

describe('dayMape', () => {
  it('averages absolute percentage errors over hours with both values', () => {
    const points = [
      { ts: 'a', lightgbm: 110, baseline_week: null, tso: 100, actual: 100 },
      { ts: 'b', lightgbm: 190, baseline_week: 200, tso: 200, actual: 200 },
      { ts: 'c', lightgbm: 50, baseline_week: 50, tso: 50, actual: null },
      { ts: 'd', lightgbm: 50, baseline_week: 50, tso: 50, actual: 0 },
    ]
    const result = dayMape(points, 'lightgbm')
    expect(result?.hours).toBe(2)
    expect(result?.mape).toBeCloseTo(7.5)
    expect(dayMape(points, 'baseline_week')).toEqual({ mape: 0, hours: 1 })
    expect(dayMape(points, 'tso')?.mape).toBe(0)
  })

  it('returns null without actuals', () => {
    const points = forecast('2026-11-21', false).points
    expect(dayMape(points, 'lightgbm')).toBeNull()
    expect(hasActuals(points)).toBe(false)
    expect(hasActuals(forecast('2026-11-20').points)).toBe(true)
  })
})

describe('headline', () => {
  it('names the LightGBM peak with its local hour', () => {
    const day = forecast('2026-11-20')
    expect(forecastPeak(day.points)).toEqual({
      value: 61_500 * 1.02,
      ts: '2026-11-20T23:00:00+01:00',
    })
    expect(headline('2026-11-20', day.points)).toBe(
      'Forecast peak for Friday 20 November: 62.7 GW at 23:00',
    )
  })

  it('keeps the first of equal peaks and handles the repeated 02:00', () => {
    const points = forecast('2026-10-25').points.map((p, i) => ({
      ...p,
      lightgbm: i === 2 || i === 3 ? 70_000 : 40_000,
    }))
    expect(forecastPeak(points)?.ts).toBe('2026-10-25T02:00:00+02:00')
    expect(headline('2026-10-25', points)).toBe(
      'Forecast peak for Sunday 25 October: 70.0 GW at 02:00',
    )
  })

  it('falls back when the model has no values', () => {
    const points = forecast('2026-11-20').points.map((p) => ({ ...p, lightgbm: null }))
    expect(headline('2026-11-20', points)).toBe('No model forecast for Friday 20 November')
  })
})

describe('backtest helpers', () => {
  it('finds metrics and orders hours numerically', () => {
    const data = backtest()
    expect(findMetric(data, 'lightgbm', 'overall', 'all')?.mape).toBe(2.4)
    expect(findMetric(data, 'tso', 'overall', 'all')).toBeUndefined()
    const hours = mapeByHour(data, 'lightgbm')
    expect(hours).toHaveLength(24)
    expect(hours[2]).toBeCloseTo(1.4)
    expect(hours[10]).toBeCloseTo(3.4)
    expect(hours[0]).toBeNull()
    expect(mapeByHour(data, 'tso').every((v) => v === null)).toBe(true)
  })
})
