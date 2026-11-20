import { coversFullDays, dataAgeSeconds, neighbours } from './days'

describe('days', () => {
  it('finds neighbours even for days missing from the list', () => {
    const days = ['2026-11-21', '2026-11-20', '2026-11-18']
    expect(neighbours(days, '2026-11-20')).toEqual({ older: '2026-11-18', newer: '2026-11-21' })
    expect(neighbours(days, '2026-11-19')).toEqual({ older: '2026-11-18', newer: '2026-11-20' })
    expect(neighbours(days, '2026-11-21')).toEqual({ older: '2026-11-20', newer: undefined })
    expect(neighbours([], '2026-11-21')).toEqual({ older: undefined, newer: undefined })
  })

  it('detects seven complete days', () => {
    const full = ['2026-11-14T00:00:00+01:00', '2026-11-20T23:00:00+01:00']
    expect(coversFullDays(full, '2026-11-14', '2026-11-20')).toBe(true)
    expect(
      coversFullDays(
        ['2026-11-14T00:00:00+01:00', '2026-11-20T13:00:00+01:00'],
        '2026-11-14',
        '2026-11-20',
      ),
    ).toBe(false)
    expect(coversFullDays([], '2026-11-14', '2026-11-20')).toBe(false)
  })

  it('ages the data by the time since the response', () => {
    expect(dataAgeSeconds(100, 1_000, 61_000)).toBe(160)
    expect(dataAgeSeconds(100, 61_000, 1_000)).toBe(100)
  })
})
