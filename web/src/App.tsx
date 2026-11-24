import { QueryClientProvider, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import type { QueryClient } from '@tanstack/react-query'

import { SharedFailureContext, useSharedFailure } from './api/failures'
import { SNAPSHOT_QUERIES, useStatus } from './api/queries'
import { BacktestSection } from './components/BacktestSection'
import { ContextSection } from './components/ContextSection'
import { ForecastSection } from './components/ForecastSection'
import { Footer } from './components/Footer'
import { Header, StaleNotice } from './components/Header'
import { ErrorNotice, Loading, QueryProblem } from './components/Notice'
import { NO_DATA_BODY, NO_DATA_TITLE } from './components/messages'
import { useFreshness } from './lib/freshness'
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

/**
 * Before the first snapshot: the forecast section as it will appear, with the first-start
 * text in place of the headline and an empty chart frame in place of the chart.
 */
function FirstStart() {
  return (
    <section className="section first-start" aria-labelledby="forecast-title">
      <h2 id="forecast-title" className="section-title eyebrow">
        Day-ahead forecast
      </h2>
      <p className="empty-state">
        <span className="empty-title">{NO_DATA_TITLE}</span> {NO_DATA_BODY}
      </p>
      <div className="chart empty-chart" aria-hidden="true" />
    </section>
  )
}

export function Dashboard() {
  const status = useStatus()
  useSnapshotRollover(status.data?.snapshot_created_at)
  const retryStatus = () => void status.refetch()
  const shared = useSharedFailure()
  const freshness = useFreshness(status.data, status.dataUpdatedAt)

  // Branch on data first: a failed background refetch keeps the last good status on screen.
  let content
  if (status.data === undefined) {
    content = status.isError ? (
      <QueryProblem what="the status" error={status.error} onRetry={retryStatus} />
    ) : (
      <Loading what="status" reserve="page" />
    )
  } else if (!status.data.initialised && status.data.snapshot_error) {
    content = (
      <ErrorNotice
        message={`The data snapshot is not available: ${status.data.snapshot_error}.`}
        onRetry={retryStatus}
      />
    )
  } else if (!status.data.initialised) {
    content = <FirstStart />
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
      <Header freshness={freshness} pending={status.data === undefined && !status.isError} />
      <main id="main">
        <StaleNotice freshness={freshness} />
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
