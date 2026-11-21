import {
  addDays,
  dateTimeBerlin,
  dayLong,
  dayPlain,
  dayShort,
  hourLabels,
  localDay,
  offsetChange,
  offsetLabel,
  wallClock,
} from './time'

describe('time helpers', () => {
  it('reads wall clock, day and offset from the timestamp string', () => {
    expect(wallClock('2026-10-25T02:00:00+01:00')).toBe('02:00')
    expect(localDay('2026-10-25T02:00:00+01:00')).toBe('2026-10-25')
    expect(offsetLabel('2026-10-25T02:00:00+02:00')).toBe('UTC+2')
    expect(offsetLabel('2026-10-25T02:00:00+01:00')).toBe('UTC+1')
    expect(offsetLabel('2026-10-25T02:00:00+05:30')).toBe('UTC+5:30')
    expect(offsetLabel('2026-10-25T02:00:00Z')).toBe('UTC')
  })

  it('formats days without the browser time zone', () => {
    expect(dayLong('2026-11-21')).toBe('Saturday 21 November')
    expect(dayShort('2026-11-21')).toBe('Sat 21 Nov 2026')
    expect(dayShort('2026-11-21', false)).toBe('Sat 21 Nov')
    expect(dayPlain('2026-01-06')).toBe('6 Jan 2026')
  })

  it('adds days across month and year ends', () => {
    expect(addDays('2026-11-20', -6)).toBe('2026-11-14')
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01')
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28')
  })

  it('formats instants in Berlin time', () => {
    expect(dateTimeBerlin('2026-11-20T09:45:00Z')).toBe('20 Nov 2026, 10:45')
    expect(dateTimeBerlin('2026-07-01T09:45:00Z')).toBe('1 Jul 2026, 11:45')
    expect(dateTimeBerlin('2026-11-20T13:15:00+01:00', 15)).toBe('20 Nov 2026, 13:30')
    expect(dateTimeBerlin('2026-11-20T13:56:12.345678+01:00')).toBe('20 Nov 2026, 13:56')
    expect(dateTimeBerlin(new Date(Date.UTC(2026, 11, 31, 23, 30)))).toBe('1 Jan 2027, 00:30')
  })
})

describe('hourLabels and offsetChange', () => {
  it('adds the offset only to an hour that occurs twice', () => {
    const autumn = [
      '2026-10-25T01:00:00+02:00',
      '2026-10-25T02:00:00+02:00',
      '2026-10-25T02:00:00+01:00',
      '2026-10-25T03:00:00+01:00',
    ]
    expect(hourLabels(autumn)).toEqual(['01:00', '02:00 (UTC+2)', '02:00 (UTC+1)', '03:00'])
    expect(offsetChange(autumn)).toBe(2)
    const spring = ['2026-03-29T01:00:00+01:00', '2026-03-29T03:00:00+02:00']
    expect(hourLabels(spring)).toEqual(['01:00', '03:00'])
    expect(offsetChange(spring)).toBe(1)
    expect(offsetChange(['2026-11-20T00:00:00+01:00', '2026-11-20T01:00:00+01:00'])).toBe(-1)
    expect(offsetChange([])).toBe(-1)
    expect(hourLabels([])).toEqual([])
  })
})
