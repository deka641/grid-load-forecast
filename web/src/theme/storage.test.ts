import {
  STORAGE_KEY,
  applyTheme,
  readPreference,
  resolveTheme,
  subscribeSystemTheme,
  systemPrefersDark,
  writePreference,
} from './storage'

describe('theme storage', () => {
  it('round-trips the preference and clears it for system', () => {
    expect(readPreference()).toBe('system')
    writePreference('dark')
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe('dark')
    expect(readPreference()).toBe('dark')
    writePreference('system')
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull()
    window.localStorage.setItem(STORAGE_KEY, 'neon')
    expect(readPreference()).toBe('system')
  })

  it('survives storage that throws', () => {
    const throwing = {
      getItem: () => {
        throw new Error('denied')
      },
      setItem: () => {
        throw new Error('denied')
      },
      removeItem: () => {
        throw new Error('denied')
      },
    }
    vi.stubGlobal('localStorage', throwing)
    expect(readPreference()).toBe('system')
    expect(() => {
      writePreference('light')
    }).not.toThrow()
  })

  it('resolves and applies themes', () => {
    expect(resolveTheme('system', true)).toBe('dark')
    expect(resolveTheme('system', false)).toBe('light')
    expect(resolveTheme('light', true)).toBe('light')
    applyTheme('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
  })

  it('works without matchMedia', () => {
    vi.stubGlobal('matchMedia', undefined)
    expect(systemPrefersDark()).toBe(false)
    const unsubscribe = subscribeSystemTheme(() => undefined)
    expect(() => {
      unsubscribe()
    }).not.toThrow()
  })

  it('subscribes to the system preference', () => {
    const add = vi.fn()
    const remove = vi.fn()
    vi.stubGlobal(
      'matchMedia',
      vi.fn(() => ({ matches: true, addEventListener: add, removeEventListener: remove })),
    )
    expect(systemPrefersDark()).toBe(true)
    const listener = () => undefined
    const unsubscribe = subscribeSystemTheme(listener)
    expect(add).toHaveBeenCalledWith('change', listener)
    unsubscribe()
    expect(remove).toHaveBeenCalledWith('change', listener)
  })
})
