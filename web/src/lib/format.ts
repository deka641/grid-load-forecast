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
