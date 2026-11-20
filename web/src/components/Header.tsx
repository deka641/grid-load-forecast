import type { Status } from '../api/client'
import { dataAgeSeconds } from '../lib/days'
import { dateTimeBerlin } from '../lib/time'
import { useNow } from '../lib/useNow'
import { ThemeToggle } from './ThemeToggle'
import { SNAPSHOT_FALLBACK } from './messages'

export const STALE_AFTER_SECONDS = 6 * 3600

export function Freshness({
  status,
  fetchedAt,
}: {
  status: Status | undefined
  fetchedAt: number
}) {
  const now = useNow(60_000)
  if (!status) {
    return null
  }
  const load = status.series.find((s) => s.key === 'load')
  const fallback =
    status.initialised && status.snapshot_error ? (
      <p className="freshness freshness-stale">{SNAPSHOT_FALLBACK}</p>
    ) : null
  if (!load?.last_ts) {
    return fallback
  }
  const stale =
    load.age_seconds !== null &&
    dataAgeSeconds(load.age_seconds, fetchedAt, now) > STALE_AFTER_SECONDS
  return (
    <>
      <p className={stale ? 'freshness freshness-stale' : 'freshness'}>
        {/* last_ts is the start of the newest quarter hour; the data covers its end. */}
        Data up to {dateTimeBerlin(load.last_ts, 15)}
        {stale ? <strong className="stale-note"> (more than 6 hours old)</strong> : null}
      </p>
      {fallback}
    </>
  )
}

export function Header({ status, fetchedAt }: { status: Status | undefined; fetchedAt: number }) {
  return (
    <header className="site-header">
      <div className="site-title">
        <h1>German grid load: day-ahead forecast</h1>
        <p className="lede">
          A day-ahead forecast of the German grid load from public SMARD data, compared with the
          forecast published there.
        </p>
      </div>
      <div className="site-meta">
        <div className="freshness-group">
          <Freshness status={status} fetchedAt={fetchedAt} />
        </div>
        <ThemeToggle />
      </div>
    </header>
  )
}
