import { isStale, STALE_AFTER_SECONDS } from './freshness'

const HOUR = 3600

describe('freshness', () => {
  it('stays silent under the normal publication delay', () => {
    // Run at 20:05 Berlin on 21 Nov 2026: the newest value ended 50 minutes earlier.
    expect(isStale(50 * 60)).toBe(false)
    // Just before the next hourly run, after a slow run.
    expect(isStale(2 * HOUR)).toBe(false)
    // Three failed runs in a row.
    expect(isStale(5 * HOUR)).toBe(false)
  })

  it('warns when the source stops publishing', () => {
    // SMARD stopped after 11:15 Berlin on 23 Nov 2026; runs at 18:30 and 23:05 Berlin.
    expect(isStale(7.25 * HOUR)).toBe(true)
    expect(isStale(11.8 * HOUR)).toBe(true)
  })

  it('warns only above the threshold', () => {
    expect(STALE_AFTER_SECONDS).toBe(6 * HOUR)
    expect(isStale(STALE_AFTER_SECONDS - 1)).toBe(false)
    expect(isStale(STALE_AFTER_SECONDS)).toBe(false)
    expect(isStale(STALE_AFTER_SECONDS + 1)).toBe(true)
  })
})
