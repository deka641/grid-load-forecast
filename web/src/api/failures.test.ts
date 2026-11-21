import { QueryClient, QueryObserver } from '@tanstack/react-query'

import { ApiError } from './client'
import { sharedFailure } from './failures'

function watched(client: QueryClient, key: string) {
  const observer = new QueryObserver(client, { queryKey: [key], enabled: false })
  const unsubscribe = observer.subscribe(() => undefined)
  return { query: client.getQueryCache().find({ queryKey: [key] }), unsubscribe }
}

const retrying = (error: Error) =>
  ({
    status: 'pending',
    fetchStatus: 'fetching',
    fetchFailureCount: 1,
    fetchFailureReason: error,
  }) as const

describe('sharedFailure', () => {
  it('names only failed sections, not one that is still retrying with the same error', () => {
    const client = new QueryClient()
    const error = new ApiError(500, 'the snapshot could not be read')
    const forecast = watched(client, 'forecast')
    const backtest = watched(client, 'backtest')
    const actuals = watched(client, 'actuals')
    forecast.query?.setState({ status: 'error', error, fetchFailureCount: 3 })
    backtest.query?.setState({ status: 'error', error, fetchFailureCount: 3 })
    // Still retrying: pending after a failed attempt, so its section shows Loading.
    actuals.query?.setState(retrying(error))
    const shared = JSON.parse(sharedFailure(client)) as { whats: string[] }
    expect(shared.whats).toEqual(['the forecast', 'the backtest'])

    // One failed section next to a retrying one is no shared failure.
    backtest.query?.setState(retrying(error))
    expect(sharedFailure(client)).toBe('')
    for (const q of [forecast, backtest, actuals]) {
      q.unsubscribe()
    }
  })
})
