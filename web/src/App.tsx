import { QueryClientProvider } from '@tanstack/react-query'
import type { QueryClient } from '@tanstack/react-query'

import { useStatus } from './api/queries'
import { BacktestSection } from './components/BacktestSection'
import { ContextSection } from './components/ContextSection'
import { ForecastSection } from './components/ForecastSection'
import { Footer } from './components/Footer'
import { Header } from './components/Header'
import { Info, Loading, QueryProblem } from './components/Notice'
import { NO_DATA } from './components/messages'
import { localDay } from './lib/time'
import { ThemeProvider } from './theme/ThemeProvider'

export function Dashboard() {
  const status = useStatus()

  let content
  if (status.isPending) {
    content = <Loading what="status" />
  } else if (status.isError) {
    content = (
      <QueryProblem what="the status" error={status.error} onRetry={() => void status.refetch()} />
    )
  } else if (!status.data.initialised) {
    content = <Info>{NO_DATA}</Info>
  } else {
    content = (
      <>
        <ForecastSection />
        <BacktestSection />
        <ContextSection today={localDay(status.data.generated_at)} />
      </>
    )
  }

  return (
    <div className="page">
      <Header status={status.data} />
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
