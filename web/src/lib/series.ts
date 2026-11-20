import type { Actuals } from '../api/client'

export const PRICE_KEY = 'price_day_ahead'

export interface Aligned {
  stamps: string[]
  values: Record<string, (number | null)[]>
}

/**
 * Align hourly series on one time grid. Instants are compared by their epoch value,
 * because sorting ISO strings with different offsets is wrong on clock-change days.
 * `gridKeys` decide which series span the grid; the others are mapped onto it.
 */
export function alignSeries(actuals: Actuals, gridKeys: readonly string[]): Aligned {
  const instants = new Map<number, string>()
  for (const series of actuals.series) {
    if (!gridKeys.includes(series.key)) {
      continue
    }
    for (const point of series.points) {
      instants.set(Date.parse(point.ts), point.ts)
    }
  }
  const ordered = [...instants.entries()].sort((a, b) => a[0] - b[0])
  const position = new Map(ordered.map(([epoch], index) => [epoch, index]))
  const values: Record<string, (number | null)[]> = {}
  for (const series of actuals.series) {
    const row: (number | null)[] = new Array<number | null>(ordered.length).fill(null)
    for (const point of series.points) {
      const index = position.get(Date.parse(point.ts))
      if (index !== undefined) {
        row[index] = point.value
      }
    }
    values[series.key] = row
  }
  return { stamps: ordered.map(([, ts]) => ts), values }
}

export interface Stats {
  min: number
  max: number
  mean: number
  negativeHours: number
  hours: number
}

export function stats(values: readonly (number | null)[]): Stats | null {
  const present = values.filter((v): v is number => v !== null)
  if (present.length === 0) {
    return null
  }
  return {
    min: Math.min(...present),
    max: Math.max(...present),
    mean: present.reduce((a, b) => a + b, 0) / present.length,
    negativeHours: present.filter((v) => v < 0).length,
    hours: present.length,
  }
}

/** Mean value per series over the hours where it has data. */
export function meanOf(values: readonly (number | null)[]): number | null {
  return stats(values)?.mean ?? null
}
