import { useQueryClient } from '@tanstack/react-query'
import type { Query, QueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useMemo, useSyncExternalStore } from 'react'

import { describeError } from '../components/messages'
import { isApiError, isNoData } from './client'

/** What each section calls its data in an error message, in page order. */
const SECTIONS: readonly { key: string; what: string }[] = [
  { key: 'forecast', what: 'the forecast' },
  { key: 'backtest', what: 'the backtest' },
  { key: 'series', what: 'the series list' },
  { key: 'actuals', what: 'the generation data' },
]

/**
 * The reason a query failed, or null. Only failed queries count: one that is still retrying
 * shows Loading in its section and must not be named in the notice. 404 and the first-start
 * 503 describe the data and keep their own texts.
 */
function failureOf(query: Query): string | null {
  const { state } = query
  const error = state.status === 'error' ? state.error : null
  if (error === null || isNoData(error) || (isApiError(error) && error.status < 500)) {
    return null
  }
  return describeError(error)
}

interface Failure {
  reason: string
  whats: string[]
}

/**
 * Sections whose data failed for the same reason, such as an unreadable snapshot.
 * Encoded as a string, so that the store snapshot only changes when the result does.
 */
export function sharedFailure(client: QueryClient): string {
  const groups = new Map<string, Failure>()
  for (const query of client.getQueryCache().getAll()) {
    const section = SECTIONS.find((s) => s.key === query.queryKey[0])
    const reason = failureOf(query)
    if (!section || reason === null || query.getObserversCount() === 0) {
      continue
    }
    const group = groups.get(reason) ?? { reason, whats: [] }
    if (!group.whats.includes(section.what)) {
      group.whats.push(section.what)
    }
    groups.set(reason, group)
  }
  const shared = [...groups.values()].find((g) => g.whats.length > 1)
  if (!shared) {
    return ''
  }
  const order = SECTIONS.map((s) => s.what)
  shared.whats.sort((a, b) => order.indexOf(a) - order.indexOf(b))
  return JSON.stringify(shared)
}

function listText(items: readonly string[]): string {
  return items.length < 2
    ? (items[0] ?? '')
    : `${items.slice(0, -1).join(', ')} and ${items.at(-1) ?? ''}`
}

export interface SharedFailure {
  reason: string
  message: string
  retry: () => void
}

export function useSharedFailure(): SharedFailure | null {
  const client = useQueryClient()
  const subscribe = useCallback(
    (onChange: () => void) => client.getQueryCache().subscribe(onChange),
    [client],
  )
  const encoded = useSyncExternalStore(subscribe, () => sharedFailure(client))
  return useMemo(() => {
    if (encoded === '') {
      return null
    }
    const { reason, whats } = JSON.parse(encoded) as Failure
    const keys = SECTIONS.filter((s) => whats.includes(s.what)).map((s) => s.key)
    return {
      reason,
      message: `Could not load ${listText(whats)}: ${reason}.`,
      retry: () => {
        void client.refetchQueries({
          predicate: (query) =>
            keys.includes(String(query.queryKey[0])) && failureOf(query) === reason,
        })
      },
    }
  }, [client, encoded])
}

/** The reason that the page already reports once for several sections, or null. */
export const SharedFailureContext = createContext<string | null>(null)

export function useSharedReason(): string | null {
  return useContext(SharedFailureContext)
}
