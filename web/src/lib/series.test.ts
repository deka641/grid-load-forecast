import { actuals } from '../test/fixtures'
import { alignSeries, meanOf, stats } from './series'

describe('alignSeries', () => {
  it('orders instants by epoch, not by string', () => {
    const data = {
      resolution: 'hour' as const,
      start: '2026-10-25',
      end: '2026-10-25',
      series: [
        {
          key: 'gen_solar',
          label: 'PV',
          unit: 'MWh',
          points: [
            { ts: '2026-10-25T02:00:00+01:00', value: 2 },
            { ts: '2026-10-25T02:00:00+02:00', value: 1 },
          ],
        },
      ],
    }
    const aligned = alignSeries(data, ['gen_solar'])
    expect(aligned.stamps).toEqual(['2026-10-25T02:00:00+02:00', '2026-10-25T02:00:00+01:00'])
    expect(aligned.values.gen_solar).toEqual([1, 2])
  })

  it('maps other series onto the grid and drops points outside it', () => {
    const aligned = alignSeries(actuals(['gen_solar', 'price_day_ahead']), ['gen_solar'])
    expect(aligned.stamps).toHaveLength(2)
    expect(aligned.values.price_day_ahead).toEqual([-5.5, 80])
  })
})

describe('stats', () => {
  it('summarises present values', () => {
    expect(stats([-2, null, 4, 7])).toEqual({
      min: -2,
      max: 7,
      mean: 3,
      negativeHours: 1,
      hours: 3,
    })
    expect(stats([null])).toBeNull()
    expect(meanOf([])).toBeNull()
    expect(meanOf([1, 3])).toBe(2)
  })
})
