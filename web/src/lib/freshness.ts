import type { Status } from '../api/client'
import { dataAgeSeconds } from './days'
import { useNow } from './useNow'

/**
 * Age of the newest load value above which the dashboard warns. In normal operation the
 * hourly run finds the load up to about 50 minutes before it starts, also in the evening, so
 * the age stays below two hours. Six hours leaves room for a few failed runs and still warns
 * on the same day when SMARD stops publishing, well before the healthcheck limit of 12 hours.
 */
export const STALE_AFTER_SECONDS = 6 * 3600

/** Whether load data of this age (seconds since the end of its newest quarter hour) is stale. */
export function isStale(ageSeconds: number): boolean {
  return ageSeconds > STALE_AFTER_SECONDS
}

export interface FreshnessState {
  /** Start of the newest quarter hour of load data, or null before the first load. */
  lastTs: string | null
  stale: boolean
  /** An older snapshot is served because the newest one cannot be read. */
  fallback: boolean
}

export function useFreshness(status: Status | undefined, fetchedAt: number): FreshnessState {
  const now = useNow(60_000)
  const load = status?.series.find((s) => s.key === 'load')
  const lastTs = load?.last_ts ?? null
  return {
    lastTs,
    stale:
      lastTs !== null &&
      load?.age_seconds !== null &&
      load?.age_seconds !== undefined &&
      isStale(dataAgeSeconds(load.age_seconds, fetchedAt, now)),
    fallback: status?.initialised === true && Boolean(status.snapshot_error),
  }
}
