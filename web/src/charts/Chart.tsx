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
  /**
   * A one-time accent after the draw-in: a series shown at `delay` ms after the first
   * animated render and removed after `duration` ms. Only with motion allowed; keep it
   * stable with useCallback.
   */
  arrival?: (palette: Palette) => ChartArrival | null
  label: string
  /** The plotted values as a table: read by screen readers, shown on request. */
  children?: ReactNode
  /** Short name of the opened data table, for its scroll region. */
  dataLabel?: string
  className?: string
}

export interface ChartArrival {
  /** A series option with an `id`, added to the chart and later emptied by that id. */
  series: { id: string; type: string } & Record<string, unknown>
  delay: number
  duration: number
}

type Instance = ReturnType<typeof echarts.init>

/** How long a render waits for the web font before it draws with the fallback font. */
const FONT_WAIT_MS = 1000

function isCompact(element: HTMLElement): boolean {
  return element.clientWidth > 0 && element.clientWidth < COMPACT_WIDTH
}

function allowsMotion(): boolean {
  return (
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: no-preference)').matches
  )
}

export function Chart({ build, arrival, label, children, dataLabel, className }: ChartProps) {
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
    // The observer reports the size once right after observe(); a resize to the size the chart
    // already has would only redraw it without animation and cut the draw-in short.
    let size = { width: element.clientWidth, height: element.clientHeight }
    const observer = new ResizeObserver(() => {
      const next = { width: element.clientWidth, height: element.clientHeight }
      if (next.width === size.width && next.height === size.height) {
        return
      }
      size = next
      chart.resize()
      setCompact(isCompact(element))
    })
    observer.observe(element)
    return () => {
      observer.disconnect()
      chart.dispose()
      instance.current = null
      // A new instance (such as after a StrictMode remount) starts empty and must be drawn.
      rendered.current = null
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
    const previous = rendered.current
    let animate = motion && previous === null
    rendered.current = { build, key }
    let drawn = false
    const accents: ReturnType<typeof setTimeout>[] = []
    const render = () => {
      if (active) {
        const palette = readPalette()
        instance.current?.setOption(build(palette, { compact: narrow, motion, animate }), {
          notMerge: true,
        })
        const accent = animate ? arrival?.(palette) : null
        if (accent) {
          // Added once the lines have drawn in, emptied once it has played.
          accents.push(
            setTimeout(() => {
              instance.current?.setOption({ series: [accent.series] })
            }, accent.delay),
            setTimeout(() => {
              // With its type, so that it is a valid series also after a full re-render.
              const { id, type } = accent.series
              instance.current?.setOption({ series: [{ id, type, data: [] }] })
            }, accent.delay + accent.duration),
          )
        }
        animate = false
        drawn = true
      }
    }
    // Canvas text uses the web font only once it has loaded; render again only then. A
    // draw-in waits for the font instead, since a second render would cut it short.
    const fontsPending = 'fonts' in document && document.fonts.status !== 'loaded'
    const deferred = !same && animate && fontsPending
    if (!same && !deferred) {
      render()
    }
    // A stalled font fetch must not leave the chart blank: after FONT_WAIT_MS it is drawn
    // with the fallback font, and the font render follows when the font arrives.
    let timer: ReturnType<typeof setTimeout> | undefined
    if (fontsPending) {
      if (deferred) {
        timer = setTimeout(render, FONT_WAIT_MS)
      }
      void document.fonts.ready.then(() => {
        clearTimeout(timer)
        render()
      })
    }
    return () => {
      active = false
      clearTimeout(timer)
      accents.forEach(clearTimeout)
      // A render still waiting for the font did not happen: the next run must still do it,
      // including the draw-in when this was the first one.
      if (!drawn) {
        rendered.current = previous
      }
    }
  }, [build, arrival, resolved, compact])

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
