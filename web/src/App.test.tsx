import { render, screen } from '@testing-library/react'

import { App } from './App'

describe('App', () => {
  it('shows the title and the data attribution', () => {
    render(<App />)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('day-ahead forecast')
    expect(screen.getByRole('link', { name: 'Bundesnetzagentur | SMARD.de' })).toHaveAttribute(
      'href',
      'https://www.smard.de',
    )
  })
})
