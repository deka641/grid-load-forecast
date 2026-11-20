import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'

import { useTheme } from '../theme/context'
import { echarts } from './echarts'
import type { EChartsCoreOption } from './echarts'
import { readPalette } from './palette'
import type { Palette } from './palette'

export interface ChartProps {
  /** Builds the option from the colours of the active theme; keep it stable with useCallback. */
  build: (palette: Palette) => EChartsCoreOption
  label: string
  /** Text alternative for screen readers, usually a table of the plotted values. */
  children?: ReactNode
  className?: string
}

type Instance = ReturnType<typeof echarts.init>

export function Chart({ build, label, children, className }: ChartProps) {
  const container = useRef<HTMLDivElement>(null)
  const instance = useRef<Instance | null>(null)
  const { resolved } = useTheme()

  useEffect(() => {
    const element = container.current
    if (!element) {
      return
    }
    const chart = echarts.init(element, null, { renderer: 'canvas' })
    instance.current = chart
    const observer = new ResizeObserver(() => {
      chart.resize()
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
    const render = () => {
      if (active) {
        instance.current?.setOption(build(readPalette()), { notMerge: true })
      }
    }
    render()
    // Canvas text uses the web font only once it has loaded.
    if ('fonts' in document) {
      void document.fonts.ready.then(render)
    }
    return () => {
      active = false
    }
  }, [build, resolved])

  return (
    <figure className={className ? `chart ${className}` : 'chart'}>
      <div ref={container} className="chart-canvas" role="img" aria-label={label} />
      {children ? <figcaption className="visually-hidden">{children}</figcaption> : null}
    </figure>
  )
}
