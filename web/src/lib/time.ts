/**
 * Time helpers for Europe/Berlin. API timestamps carry the Berlin offset, so the
 * wall-clock parts are read from the string itself and never depend on the
 * browser time zone. Calendar arithmetic on plain days runs in UTC.
 */

const BERLIN = 'Europe/Berlin'

const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
const MONTHS = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
]

function parseDay(day: string): Date {
  return new Date(
    Date.UTC(Number(day.slice(0, 4)), Number(day.slice(5, 7)) - 1, Number(day.slice(8, 10))),
  )
}

function pick(list: readonly string[], index: number): string {
  return list[index] ?? ''
}

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

/** "HH:MM" of a Berlin-offset ISO timestamp. */
export function wallClock(ts: string): string {
  return ts.slice(11, 16)
}

/** The local day "YYYY-MM-DD" of a Berlin-offset ISO timestamp. */
export function localDay(ts: string): string {
  return ts.slice(0, 10)
}

/** "UTC+1" or "UTC+2" from a Berlin-offset ISO timestamp. */
export function offsetLabel(ts: string): string {
  if (!/[+-]\d{2}:\d{2}$/.test(ts)) {
    return 'UTC'
  }
  const sign = ts.slice(-6, -5)
  const hours = String(Number(ts.slice(-5, -3)))
  const minutes = ts.slice(-2)
  return minutes === '00' ? `UTC${sign}${hours}` : `UTC${sign}${hours}:${minutes}`
}

/** "Friday 21 November" */
export function dayLong(day: string): string {
  const date = parseDay(day)
  return `${pick(WEEKDAYS, date.getUTCDay())} ${String(date.getUTCDate())} ${pick(MONTHS, date.getUTCMonth())}`
}

/** "Fri 21 Nov 2026" */
export function dayShort(day: string, withYear = true): string {
  const date = parseDay(day)
  const weekday = pick(WEEKDAYS, date.getUTCDay()).slice(0, 3)
  const month = pick(MONTHS, date.getUTCMonth()).slice(0, 3)
  const base = `${weekday} ${String(date.getUTCDate())} ${month}`
  return withYear ? `${base} ${String(date.getUTCFullYear())}` : base
}

/** "21 Nov 2026" */
export function dayPlain(day: string): string {
  const date = parseDay(day)
  const month = pick(MONTHS, date.getUTCMonth()).slice(0, 3)
  return `${String(date.getUTCDate())} ${month} ${String(date.getUTCFullYear())}`
}

export function addDays(day: string, delta: number): string {
  const date = parseDay(day)
  date.setUTCDate(date.getUTCDate() + delta)
  return `${String(date.getUTCFullYear())}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`
}

const BERLIN_FORMAT = new Intl.DateTimeFormat('en-GB', {
  timeZone: BERLIN,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

/** Berlin wall-clock parts of an instant, independent of the browser time zone. */
export function berlinParts(instant: Date): { day: string; time: string } {
  const parts = new Map(BERLIN_FORMAT.formatToParts(instant).map((p) => [p.type, p.value]))
  const get = (type: Intl.DateTimeFormatPartTypes) => parts.get(type) ?? ''
  return {
    day: `${get('year')}-${get('month')}-${get('day')}`,
    time: `${get('hour')}:${get('minute')}`,
  }
}

/** "20 Nov 2026, 10:45" in Berlin time for any ISO timestamp or instant. */
export function dateTimeBerlin(value: string | Date, addMinutes = 0): string {
  const instant = new Date(typeof value === 'string' ? Date.parse(value) : value.getTime())
  instant.setTime(instant.getTime() + addMinutes * 60_000)
  const { day, time } = berlinParts(instant)
  return `${dayPlain(day)}, ${time}`
}
