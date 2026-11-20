import { Suspense, lazy } from 'react'

import type { ChartProps } from './Chart'

// ECharts is the largest dependency; it loads in its own chunk after the first paint.
const ChartImpl = lazy(() => import('./Chart').then((module) => ({ default: module.Chart })))

export function Chart(props: ChartProps) {
  return (
    <Suspense
      fallback={
        <figure className={props.className ? `chart ${props.className}` : 'chart'}>
          <div className="chart-canvas" role="img" aria-label={props.label} aria-busy="true" />
        </figure>
      }
    >
      <ChartImpl {...props} />
    </Suspense>
  )
}
