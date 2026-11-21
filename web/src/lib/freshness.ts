import type { Status } from '../api/client'
import { dataAgeSeconds } from './days'
import { useNow } from './useNow'

export const STALE_AFTER_SECONDS = 6 * 3600

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
      dataAgeSeconds(load.age_seconds, fetchedAt, now) > STALE_AFTER_SECONDS,
    fallback: status?.initialised === true && Boolean(status.snapshot_error),
  }
}
