import { render, screen } from '@testing-library/react'

import { Legend } from './Legend'

describe('Legend', () => {
  it('draws dash patterns for lines and swatches for areas', () => {
    render(
      <Legend
        label="Items"
        items={[
          { key: 'actual', label: 'Actual' },
          { key: 'baseline_week', label: 'Baseline', pattern: 'dashed', width: 3 },
          { key: 'gen_solar', label: 'Photovoltaics', area: true },
        ]}
      />,
    )
    const list = screen.getByRole('list', { name: 'Items' })
    const lines = list.querySelectorAll('line')
    expect(lines[0]?.getAttribute('stroke-dasharray')).toBeNull()
    expect(lines[0]?.getAttribute('stroke-width')).toBe('2')
    expect(lines[1]?.getAttribute('stroke-dasharray')).toBe('6 4')
    expect(list.querySelector('.swatch-gen_solar')).not.toBeNull()
    expect(list.querySelector('.is-empty')).toBeNull()
  })

  it('mutes a series that has no values yet', () => {
    render(
      <Legend
        label="Items"
        items={[
          { key: 'actual', label: 'Actual', empty: true },
          { key: 'lightgbm', label: 'Model' },
        ]}
      />,
    )
    expect(screen.getByText('Actual (not yet published)')).toHaveClass('legend-item', 'is-empty')
    expect(screen.getByText('Model')).not.toHaveClass('is-empty')
  })
})
