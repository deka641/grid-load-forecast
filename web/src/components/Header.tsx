import type { FreshnessState } from '../lib/freshness'
import { dateTimeBerlin } from '../lib/time'
import { ThemeToggle } from './ThemeToggle'
import { SNAPSHOT_FALLBACK } from './messages'

/** last_ts is the start of the newest quarter hour; the data covers its end. */
function dataUpTo(lastTs: string): string {
  return `Data up to ${dateTimeBerlin(lastTs, 15)}`
}

/** Shown across the page while the data is old, so that it is not missed in the header. */
export function StaleNotice({ freshness }: { freshness: FreshnessState }) {
  if (!freshness.stale || freshness.lastTs === null) {
    return null
  }
  return (
    <p className="freshness freshness-stale stale-banner">
      {dataUpTo(freshness.lastTs)}
      <strong className="stale-note"> (more than 6 hours old)</strong>
    </p>
  )
}

/** A load curve over a day, the mark of the page and its favicon. */
function Mark() {
  return (
    <svg className="site-mark" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <rect width="32" height="32" rx="6" />
      <path d="M5 21.5 C8 23 10 22.5 12 18 S15 10.5 17.5 11.5 S20.5 13.5 22.5 9.5 S25.5 12 27 17" />
    </svg>
  )
}

export function Header({ freshness }: { freshness: FreshnessState }) {
  const { lastTs, fallback } = freshness
  return (
    <header className="site-header">
      <div className="site-title">
        <h1>
          <Mark />
          German grid load: day-ahead forecast
        </h1>
        <p className="lede">
          A day-ahead forecast of the German grid load from public SMARD data, compared with the
          forecast published there.
        </p>
      </div>
      <div className="site-meta">
        {/* Without any line the group is left out, so the toggle lines up with the title. */}
        {lastTs !== null || fallback ? (
          <div className="freshness-group">
            {lastTs === null ? null : <p className="freshness">{dataUpTo(lastTs)}</p>}
            {fallback ? <p className="freshness freshness-stale">{SNAPSHOT_FALLBACK}</p> : null}
          </div>
        ) : null}
        <ThemeToggle />
      </div>
    </header>
  )
}
