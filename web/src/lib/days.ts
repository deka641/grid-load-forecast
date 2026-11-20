import { localDay, wallClock } from './time'

/** True when the hours span seven complete local days ending with `end`. */
export function coversFullDays(stamps: readonly string[], start: string, end: string): boolean {
  const first = stamps[0]
  const last = stamps.at(-1)
  return (
    first !== undefined &&
    last !== undefined &&
    localDay(first) === start &&
    wallClock(first) === '00:00' &&
    localDay(last) === end &&
    wallClock(last) === '23:00'
  )
}

/** Neighbours of the shown day in the list (newest first), even if the day is not listed. */
export function neighbours(days: readonly string[], current: string) {
  return {
    older: days.find((d) => d < current),
    newer: days.findLast((d) => d > current),
  }
}

/**
 * Age of the load data now: the API reports it at response time, so the time since
 * that response is added. The stale warning can thus appear in a tab left open.
 */
export function dataAgeSeconds(ageSeconds: number, fetchedAt: number, now: number): number {
  return ageSeconds + Math.max(0, now - fetchedAt) / 1000
}
