import { QueryClientProvider, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import type { QueryClient } from '@tanstack/react-query'

import { SharedFailureContext, useSharedFailure } from './api/failures'
import { SNAPSHOT_QUERIES, useStatus } from './api/queries'
import { BacktestSection } from './components/BacktestSection'
import { ContextSection } from './components/ContextSection'
import { ForecastSection } from './components/ForecastSection'
import { Footer } from './components/Footer'
import { Header } from './components/Header'
import { ErrorNotice, Info, Loading, QueryProblem } from './components/Notice'
import { NO_DATA } from './components/messages'
import { dateTimeBerlin, localDay } from './lib/time'
import { ThemeProvider } from './theme/ThemeProvider'

/** Refetch snapshot data when the API starts serving a new snapshot. */
function useSnapshotRollover(createdAt: string | null | undefined) {
  const client = useQueryClient()
  const previous = useRef(createdAt)
  useEffect(() => {
    if (previous.current && createdAt && previous.current !== createdAt) {
      for (const queryKey of SNAPSHOT_QUERIES) {
        void client.invalidateQueries({ queryKey })
      }
    }
    previous.current = createdAt
  }, [client, createdAt])
}

export function Dashboard() {
  const status = useStatus()
  useSnapshotRollover(status.data?.snapshot_created_at)
  const retryStatus = () => void status.refetch()
  const shared = useSharedFailure()

  // Branch on data first: a failed background refetch keeps the last good status on screen.
  let content
  if (status.data === undefined) {
    content = status.isError ? (
      <QueryProblem what="the status" error={status.error} onRetry={retryStatus} />
    ) : (
      <Loading what="status" />
    )
  } else if (!status.data.initialised && status.data.snapshot_error) {
    content = (
      <ErrorNotice
        message={`The data snapshot is not available: ${status.data.snapshot_error}.`}
        onRetry={retryStatus}
      />
    )
  } else if (!status.data.initialised) {
    content = <Info>{NO_DATA}</Info>
  } else {
    content = (
      <SharedFailureContext value={shared?.reason ?? null}>
        {/* One notice for a failure behind several sections, instead of one per section. */}
        {shared ? <ErrorNotice message={shared.message} onRetry={shared.retry} /> : null}
        <ForecastSection />
        <BacktestSection />
        <ContextSection
          today={localDay(status.data.generated_at)}
          priceNote={status.data.price_note}
        />
      </SharedFailureContext>
    )
  }

  return (
    <div className="page">
      <Header status={status.data} fetchedAt={status.dataUpdatedAt} />
      <main id="main">
        {status.isError && status.data ? (
          <p className="inline-note refresh-note" role="status">
            Could not refresh the status; showing data from{' '}
            {dateTimeBerlin(status.data.generated_at)}.
          </p>
        ) : null}
        {content}
      </main>
      <Footer status={status.data} />
    </div>
  )
}

export function App({ client }: { client: QueryClient }) {
  return (
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <Dashboard />
      </ThemeProvider>
    </QueryClientProvider>
  )
}
