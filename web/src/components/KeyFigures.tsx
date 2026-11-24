import type { BacktestMetrics } from '../api/client'
import { NOT_AVAILABLE, formatPercent } from '../lib/format'
import { MODELS, findMetric } from '../lib/metrics'

/**
 * The overall backtest MAPE of each forecast, from the backtest the page loads anyway, so
 * the peak can be read against how accurate the model has been. No hour count: the SMARD
 * forecast is scored only on the hours SMARD published, so the values have different bases;
 * the backtest section below names them.
 */
export function KeyFigures({ backtest }: { backtest: BacktestMetrics | null }) {
  // While the backtest loads, an invisible copy holds the place of the band, so the chart
  // does not move down when the values arrive.
  const pending = backtest === null
  return (
    <div
      className={pending ? 'key-figures key-figures-pending' : 'key-figures'}
      aria-hidden={pending ? true : undefined}
    >
      <p className="key-figures-label">Backtest MAPE</p>
      <dl>
        {MODELS.map((m) => {
          const metric = backtest ? findMetric(backtest, m.key, 'overall', 'all') : undefined
          return (
            <div key={m.key} className={`key-figure key-${m.key}`}>
              <dt>
                <span className={`row-key stroke-${m.key}`} aria-hidden="true" />
                {m.short}
              </dt>
              <dd>{pending ? '\u00a0' : metric ? formatPercent(metric.mape) : NOT_AVAILABLE}</dd>
            </div>
          )
        })}
      </dl>
    </div>
  )
}
