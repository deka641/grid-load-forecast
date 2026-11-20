import { QueryClientProvider, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import type { QueryClient } from '@tanstack/react-query'

import { SNAPSHOT_QUERIES, useStatus } from './api/queries'
import { BacktestSection } from './components/BacktestSection'
import { ContextSection } from './components/ContextSection'
import { ForecastSection } from './components/ForecastSection'
import { Footer } from './components/Footer'
import { Header } from './components/Header'
import { ErrorNotice, Info, Loading, QueryProblem } from './components/Notice'
import { NO_DATA } from './components/messages'
import { localDay } from './lib/time'
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

  let content
  if (status.isPending) {
    content = <Loading what="status" />
  } else if (status.isError) {
    content = <QueryProblem what="the status" error={status.error} onRetry={retryStatus} />
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
      <>
        <ForecastSection />
        <BacktestSection />
        <ContextSection
          today={localDay(status.data.generated_at)}
          priceNote={status.data.price_note}
        />
      </>
    )
  }

  return (
    <div className="page">
      <Header status={status.data} fetchedAt={status.dataUpdatedAt} />
      <main id="main">{content}</main>
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
