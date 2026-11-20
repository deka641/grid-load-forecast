import type { Status } from '../api/client'
import { dateTimeBerlin } from '../lib/time'
import { ThemeToggle } from './ThemeToggle'

export const STALE_AFTER_SECONDS = 6 * 3600

export function Freshness({ status }: { status: Status | undefined }) {
  const load = status?.series.find((s) => s.key === 'load')
  if (!load?.last_ts) {
    return null
  }
  const stale = load.age_seconds !== null && load.age_seconds > STALE_AFTER_SECONDS
  return (
    <p className={stale ? 'freshness freshness-stale' : 'freshness'}>
      {/* last_ts is the start of the newest quarter hour; the data covers its end. */}
      Data up to {dateTimeBerlin(load.last_ts, 15)}
      {stale ? <strong className="stale-note"> (more than 6 hours old)</strong> : null}
    </p>
  )
}

export function Header({ status }: { status: Status | undefined }) {
  return (
    <header className="site-header">
      <div className="site-title">
        <h1>German grid load: day-ahead forecast</h1>
        <p className="lede">
          An hourly forecast of tomorrow&apos;s electricity demand in Germany, trained on public
          SMARD data and compared with the forecast published there.
        </p>
      </div>
      <div className="site-meta">
        <Freshness status={status} />
        <ThemeToggle />
      </div>
    </header>
  )
}
