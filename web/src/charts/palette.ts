export interface Palette {
  ink: string
  muted: string
  rule: string
  grid: string
  surface: string
  tooltipBg: string
  tooltipBorder: string
  model: string
  baseline: string
  tso: string
  actual: string
  price: string
  /** Glow around the model line and its peak dot; transparent where the theme has none. */
  glow: string
  fontFamily: string
  generation: Record<string, string>
}

/** Generation series keys in stacking order, bottom to top, with their CSS variable. */
export const GENERATION: readonly { key: string; variable: string }[] = [
  { key: 'gen_nuclear', variable: '--gen-nuclear' },
  { key: 'gen_lignite', variable: '--gen-lignite' },
  { key: 'gen_hard_coal', variable: '--gen-hard-coal' },
  { key: 'gen_gas', variable: '--gen-gas' },
  { key: 'gen_other_conventional', variable: '--gen-other-conventional' },
  { key: 'gen_biomass', variable: '--gen-biomass' },
  { key: 'gen_hydro', variable: '--gen-hydro' },
  { key: 'gen_pumped_storage', variable: '--gen-pumped-storage' },
  { key: 'gen_other_renewable', variable: '--gen-other-renewable' },
  { key: 'gen_wind_offshore', variable: '--gen-wind-offshore' },
  { key: 'gen_wind_onshore', variable: '--gen-wind-onshore' },
  { key: 'gen_solar', variable: '--gen-solar' },
]

const FALLBACK = '#888888'

/** Read the chart colours of the active theme from the CSS custom properties on :root. */
export function readPalette(root: HTMLElement = document.documentElement): Palette {
  const style = getComputedStyle(root)
  const read = (name: string) => style.getPropertyValue(name).trim() || FALLBACK
  const generation: Record<string, string> = {}
  for (const { key, variable } of GENERATION) {
    generation[key] = read(variable)
  }
  return {
    ink: read('--color-ink'),
    muted: read('--color-muted'),
    rule: read('--color-rule'),
    grid: read('--color-grid'),
    surface: read('--color-surface'),
    tooltipBg: read('--color-tooltip-bg'),
    tooltipBorder: read('--color-tooltip-border'),
    model: read('--color-model'),
    baseline: read('--color-baseline'),
    tso: read('--color-tso'),
    actual: read('--color-actual'),
    price: read('--color-price'),
    glow: style.getPropertyValue('--chart-glow').trim() || 'transparent',
    fontFamily: style.getPropertyValue('--font-body').trim() || 'sans-serif',
    generation,
  }
}

export function generationColor(palette: Palette, key: string): string {
  return palette.generation[key] ?? palette.muted
}
