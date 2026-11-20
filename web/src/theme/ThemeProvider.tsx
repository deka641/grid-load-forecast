import { useCallback, useLayoutEffect, useMemo, useState, useSyncExternalStore } from 'react'
import type { ReactNode } from 'react'

import { ThemeContext } from './context'
import {
  applyTheme,
  readPreference,
  resolveTheme,
  subscribeSystemTheme,
  systemPrefersDark,
  writePreference,
} from './storage'
import type { ThemePreference } from './storage'

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>(readPreference)
  const prefersDark = useSyncExternalStore(subscribeSystemTheme, systemPrefersDark, () => false)
  const resolved = resolveTheme(preference, prefersDark)

  // Layout effects run before the charts' passive effects read the CSS variables.
  useLayoutEffect(() => {
    applyTheme(resolved)
  }, [resolved])

  const setPreference = useCallback((next: ThemePreference) => {
    writePreference(next)
    setPreferenceState(next)
  }, [])

  const value = useMemo(
    () => ({ preference, resolved, setPreference }),
    [preference, resolved, setPreference],
  )
  return <ThemeContext value={value}>{children}</ThemeContext>
}
