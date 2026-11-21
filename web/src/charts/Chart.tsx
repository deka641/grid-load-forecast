import { useEffect, useId, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { useTheme } from '../theme/context'
import { echarts } from './echarts'
import type { EChartsCoreOption } from './echarts'
import { COMPACT_WIDTH } from './options'
import type { ChartLayout } from './options'
import { readPalette } from './palette'
import type { Palette } from './palette'

export interface ChartProps {
  /**
   * Builds the option from the colours of the active theme and the layout of the chart;
   * keep it stable with useCallback.
   */
  build: (palette: Palette, layout: ChartLayout) => EChartsCoreOption
  label: string
  /** The plotted values as a table: read by screen readers, shown on request. */
  children?: ReactNode
  /** Short name of the opened data table, for its scroll region. */
  dataLabel?: string
  className?: string
}

type Instance = ReturnType<typeof echarts.init>

function isCompact(element: HTMLElement): boolean {
  return element.clientWidth > 0 && element.clientWidth < COMPACT_WIDTH
}

function allowsMotion(): boolean {
  return (
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: no-preference)').matches
  )
}

export function Chart({ build, label, children, dataLabel, className }: ChartProps) {
  const container = useRef<HTMLDivElement>(null)
  const instance = useRef<Instance | null>(null)
  const { resolved } = useTheme()
  const [showData, setShowData] = useState(false)
  // Changes only when the width crosses COMPACT_WIDTH, so a resize renders again only then.
  const [compact, setCompact] = useState(false)
  const rendered = useRef<{ build: ChartProps['build']; key: string } | null>(null)
  const dataId = useId()

  useEffect(() => {
    const element = container.current
    if (!element) {
      return
    }
    const chart = echarts.init(element, null, { renderer: 'canvas' })
    instance.current = chart
    setCompact(isCompact(element))
    const observer = new ResizeObserver(() => {
      chart.resize()
      setCompact(isCompact(element))
    })
    observer.observe(element)
    return () => {
      observer.disconnect()
      chart.dispose()
      instance.current = null
    }
  }, [])

  useEffect(() => {
    let active = true
    const element = container.current
    // The width is read from the element itself, so the first render already has it.
    const narrow = element ? isCompact(element) : compact
    const key = `${resolved}|${String(narrow)}`
    // A width change that does not cross COMPACT_WIDTH needs no new option.
    const same = rendered.current?.build === build && rendered.current.key === key
    const motion = allowsMotion()
    // Only the very first render draws in; later ones would replay it on every change.
    let animate = motion && rendered.current === null
    rendered.current = { build, key }
    const render = () => {
      if (active) {
        instance.current?.setOption(build(readPalette(), { compact: narrow, motion, animate }), {
          notMerge: true,
        })
        animate = false
      }
    }
    // Canvas text uses the web font only once it has loaded; render again only then. A
    // draw-in waits for the font instead, since a second render would cut it short.
    const fontsPending = 'fonts' in document && document.fonts.status !== 'loaded'
    if (!same && !(animate && fontsPending)) {
      render()
    }
    if (fontsPending) {
      void document.fonts.ready.then(render)
    }
    return () => {
      active = false
    }
  }, [build, resolved, compact])

  return (
    <figure className={className ? `chart ${className}` : 'chart'}>
      <div ref={container} className="chart-canvas" role="img" aria-label={label} />
      {children ? (
        <>
          <button
            type="button"
            className="chart-data-toggle"
            aria-expanded={showData}
            aria-controls={dataId}
            onClick={() => {
              setShowData((open) => !open)
            }}
          >
            {showData ? 'Hide data' : 'Show data'}
          </button>
          <figcaption id={dataId} className={showData ? 'chart-data' : 'visually-hidden'}>
            {/* Once open, the table scrolls in its own box, which keyboard users can focus. */}
            <div
              className="chart-data-scroll"
              {...(showData
                ? { tabIndex: 0, role: 'region', 'aria-label': dataLabel ?? 'Chart data' }
                : {})}
            >
              {children}
            </div>
          </figcaption>
        </>
      ) : null}
    </figure>
  )
}
