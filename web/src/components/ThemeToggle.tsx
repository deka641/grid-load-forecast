import { useTheme } from '../theme/context'
import type { ThemePreference } from '../theme/storage'

const OPTIONS: readonly { value: ThemePreference; label: string }[] = [
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
  { value: 'system', label: 'System' },
]

export function ThemeToggle() {
  const { preference, setPreference } = useTheme()
  return (
    <fieldset className="theme-toggle">
      <legend className="visually-hidden">Colour theme</legend>
      {OPTIONS.map((option) => (
        <label key={option.value} className="theme-option">
          <input
            type="radio"
            name="theme"
            value={option.value}
            checked={preference === option.value}
            onChange={() => {
              setPreference(option.value)
            }}
          />
          <span>{option.label}</span>
        </label>
      ))}
    </fieldset>
  )
}
