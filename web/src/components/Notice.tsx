import { isApiError, isNoData } from '../api/client'
import { NO_DATA, describeError } from './messages'

export function Loading({ what }: { what: string }) {
  return (
    <p className="notice notice-loading" role="status">
      Loading {what}
    </p>
  )
}

export function Info({ children }: { children: string }) {
  return <p className="notice">{children}</p>
}

export function ErrorNotice({
  what,
  error,
  onRetry,
}: {
  what: string
  error: unknown
  onRetry: () => void
}) {
  return (
    <div className="notice notice-error" role="alert">
      <p>
        Could not load {what}: {describeError(error)}.
      </p>
      <button type="button" className="button" onClick={onRetry}>
        Retry
      </button>
    </div>
  )
}

/** Shared handling of a failed query: 503 and the given 404 text are data states, not errors. */
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
  if (isNoData(error)) {
    return <Info>{NO_DATA}</Info>
  }
  if (notFound !== undefined && isApiError(error, 404)) {
    return <Info>{notFound}</Info>
  }
  return <ErrorNotice what={what} error={error} onRetry={onRetry} />
}
