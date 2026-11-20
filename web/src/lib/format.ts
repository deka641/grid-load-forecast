/** Values are MWh per hour, which equals the average power in MW. */
export function toGW(mwh: number): number {
  return mwh / 1000
}

export function formatGW(mwh: number, decimals = 1): string {
  return `${toGW(mwh).toFixed(decimals)} GW`
}

export function formatPercent(value: number, decimals = 1): string {
  return `${value.toFixed(decimals)} %`
}

export function formatPrice(value: number): string {
  return `${value.toFixed(2)} EUR/MWh`
}

export const NOT_AVAILABLE = 'n/a'

const RUN_STATUS: Record<string, string> = {
  ok: 'succeeded',
  partial: 'partly succeeded',
  failed: 'failed',
  aborted: 'aborted',
  running: 'running',
}

const RUN_MODE: Record<string, string> = {
  incremental: 'incremental run',
  backfill: 'backfill run',
}

/** Plain words for the pipeline run; an unknown value is shown as it is. */
export function describeRun(status: string, mode: string): string {
  return `${RUN_STATUS[status] ?? status}, ${RUN_MODE[mode] ?? mode}`
}
