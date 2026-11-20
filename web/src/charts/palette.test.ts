import { generationColor, readPalette } from './palette'

describe('readPalette', () => {
  afterEach(() => {
    document.documentElement.removeAttribute('style')
  })

  it('reads CSS custom properties and falls back when missing', () => {
    const root = document.documentElement
    root.style.setProperty('--color-model', ' #1d5fbf ')
    root.style.setProperty('--gen-solar', '#e9c32d')
    root.style.setProperty('--font-body', "'Barlow', sans-serif")
    const palette = readPalette()
    expect(palette.model).toBe('#1d5fbf')
    expect(palette.ink).toBe('#888888')
    expect(palette.fontFamily).toBe("'Barlow', sans-serif")
    expect(generationColor(palette, 'gen_solar')).toBe('#e9c32d')
    expect(generationColor(palette, 'gen_unknown')).toBe(palette.muted)
  })

  it('uses a generic font without the variable', () => {
    expect(readPalette().fontFamily).toBe('sans-serif')
  })
})
