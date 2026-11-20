import { createContext, useContext } from 'react'

import type { ResolvedTheme, ThemePreference } from './storage'

export interface ThemeState {
  preference: ThemePreference
  resolved: ResolvedTheme
  setPreference: (preference: ThemePreference) => void
}

export const ThemeContext = createContext<ThemeState>({
  preference: 'system',
  resolved: 'light',
  setPreference: () => undefined,
})

export function useTheme(): ThemeState {
  return useContext(ThemeContext)
}
