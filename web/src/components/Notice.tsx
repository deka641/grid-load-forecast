import { isApiError, isNoData } from '../api/client'
import { useSharedReason } from '../api/failures'
import { NO_DATA, describeError } from './messages'

/**
 * `reserve` keeps about the final height of a section's content free while it loads,
 * so the page does not jump when the charts arrive.
 */
export function Loading({
  what,
  reserve,
}: {
  what: string
  reserve?: 'forecast' | 'backtest' | 'context'
}) {
  const notice = (
    <p className="notice notice-loading" role="status">
      Loading {what}
    </p>
  )
  return reserve === undefined ? notice : <div className={`loading-${reserve}`}>{notice}</div>
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
