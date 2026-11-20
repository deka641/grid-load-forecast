import type { LinePattern } from '../charts/options'

const DASH: Record<LinePattern, string | undefined> = {
  solid: undefined,
  dashed: '6 4',
  dashdot: '8 3 2 3',
}

export interface LegendItem {
  key: string
  label: string
  pattern?: LinePattern
  width?: number
  /** Area swatch instead of a line sample. */
  area?: boolean
}

export function Legend({ items, label }: { items: readonly LegendItem[]; label: string }) {
  return (
    <ul className="legend" aria-label={label}>
      {items.map((item) => (
        <li key={item.key} className="legend-item">
          {item.area ? (
            <span className={`legend-area swatch-${item.key}`} aria-hidden="true" />
          ) : (
            <svg
              className={`legend-line stroke-${item.key}`}
              width="28"
              height="10"
              viewBox="0 0 28 10"
              aria-hidden="true"
            >
              <line
                x1="0"
                y1="5"
                x2="28"
                y2="5"
                strokeWidth={item.width ?? 2}
                strokeDasharray={DASH[item.pattern ?? 'solid']}
              />
            </svg>
          )}
          {item.label}
        </li>
      ))}
    </ul>
  )
}
