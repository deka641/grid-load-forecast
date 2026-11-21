/**
 * Plain-language names of the failed input checks of a degraded forecast. The checks
 * and their meaning are listed in docs/reference/api.md under "Degraded forecasts".
 */

const LAG_DAYS: Record<string, string> = {
  lag_1d: 'one',
  lag_2d: 'two',
  lag_7d: 'seven',
  lag_14d: '14',
}

const MEAN_HOURS: Record<string, string> = {
  mean_24h: '24',
  mean_168h: '168',
}

const KNOWN = new Set([
  'cutoff_hour_missing',
  'coverage_168h',
  ...Object.keys(LAG_DAYS),
  ...Object.keys(MEAN_HOURS),
])

/** "a", "a and b", "a, b and c" */
export function joinWords(words: readonly string[]): string {
  if (words.length <= 1) {
    return words[0] ?? ''
  }
  return `${words.slice(0, -1).join(', ')} and ${words[words.length - 1] ?? ''}`
}

/**
 * The missing inputs as short phrases in a fixed order. Related checks share one
 * phrase; check names this dashboard does not know end up in one generic phrase.
 */
export function missingInputs(checks: readonly string[] | null | undefined): string[] {
  const failed = new Set(checks ?? [])
  const phrases: string[] = []
  if (failed.has('cutoff_hour_missing')) {
    phrases.push('the load of the last hour before the cutoff (09:00 to 10:00 on the day before)')
  }
  const lags = Object.keys(LAG_DAYS).filter((key) => failed.has(key))
  if (lags.length > 0) {
    const days = joinWords(lags.map((key) => LAG_DAYS[key] ?? key))
    const unit = days === 'one' ? 'day' : 'days'
    phrases.push(`for some hours, the load of the same hour ${days} ${unit} earlier`)
  }
  const means = Object.keys(MEAN_HOURS).filter((key) => failed.has(key))
  if (means.length > 0) {
    const hours = joinWords(means.map((key) => MEAN_HOURS[key] ?? key))
    phrases.push(`the mean load of the ${hours} hours before the cutoff`)
  }
  if (failed.has('coverage_168h')) {
    phrases.push('more than 10 % of the load values of the 168 hours before the cutoff')
  }
  if ([...failed].some((check) => !KNOWN.has(check))) {
    phrases.push(phrases.length === 0 ? 'some of its input data' : 'other input data')
  }
  return phrases
}
