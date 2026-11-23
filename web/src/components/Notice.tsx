import { isApiError, isNoData } from '../api/client'
import { useSharedReason } from '../api/failures'
import { NO_DATA, describeError } from './messages'

type Reserve = 'forecast' | 'backtest' | 'context'

function Block({ kind }: { kind: string }) {
  return <span className={`skeleton-block skeleton-${kind}`} />
}

/** Quiet blocks in the place and about the size of what a section shows once loaded. */
function Skeleton({ reserve }: { reserve: Reserve }) {
  return (
    <div className={`skeleton skeleton-for-${reserve}`} aria-hidden="true">
      {reserve === 'forecast' ? (
        <>
          <Block kind="lead" />
          <Block kind="figure" />
          <Block kind="legend" />
          <Block kind="chart skeleton-chart-forecast" />
        </>
      ) : null}
      {reserve === 'backtest' ? (
        <>
          <Block kind="line" />
          <Block kind="line skeleton-line-short" />
          <span className="skeleton-grid">
            <Block kind="table" />
            <Block kind="chart skeleton-chart-small" />
          </span>
        </>
      ) : null}
      {reserve === 'context' ? (
        <>
          <Block kind="line" />
          <Block kind="legend" />
          <Block kind="chart skeleton-chart-mix" />
          <Block kind="legend" />
          <Block kind="chart skeleton-chart-price" />
        </>
      ) : null}
    </div>
  )
}

const SECTIONS: readonly Reserve[] = ['forecast', 'backtest', 'context']

/**
 * `reserve` keeps about the final height of a section's content free while it loads, with
 * quiet blocks in the final layout, so the page does not jump when the charts arrive.
 * `page` does the same for all three sections while the status loads.
 */
export function Loading({ what, reserve }: { what: string; reserve?: Reserve | 'page' }) {
  const notice = (
    <p className="notice notice-loading" role="status">
      Loading {what}
    </p>
  )
  if (reserve === undefined) {
    return notice
  }
  if (reserve === 'page') {
    return (
      <div className="loading-page">
        {notice}
        {SECTIONS.map((section) => (
          <div key={section} className={`skeleton-section loading-${section}`} aria-hidden="true">
            <Block kind="title" />
            <Skeleton reserve={section} />
          </div>
        ))}
      </div>
    )
  }
  return (
    <div className={`loading-${reserve}`}>
      {notice}
      <Skeleton reserve={reserve} />
    </div>
  )
}

export function Info({ children }: { children: string }) {
  return <p className="notice">{children}</p>
}

export function ErrorNotice({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="notice notice-error" role="alert">
      <p>{message}</p>
      <button type="button" className="button" onClick={onRetry}>
        Retry
      </button>
    </div>
  )
}

/**
 * Shared handling of a failed query: the first-start 503 and the given 404 text are data
 * states. A failure that the page already reports once for several sections shows one quiet
 * line that points to that notice, so the section does not end at its title.
 */
export function QueryProblem({
  what,
  error,
  notFound,
  onRetry,
}: {
  what: string
  error: unknown
  notFound?: string
  onRetry: () => void
}) {
  const shared = useSharedReason()
  if (isNoData(error)) {
    return <Info>{NO_DATA}</Info>
  }
  if (notFound !== undefined && isApiError(error, 404)) {
    return <Info>{notFound}</Info>
  }
  const reason = describeError(error)
  if (reason === shared) {
    return <p className="note shared-failure">Could not load {what}; see the notice above.</p>
  }
  return <ErrorNotice message={`Could not load ${what}: ${reason}.`} onRetry={onRetry} />
}
