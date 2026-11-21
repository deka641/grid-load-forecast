import { joinWords, missingInputs } from './degraded'

describe('joinWords', () => {
  it('joins one, two and more words', () => {
    expect(joinWords([])).toBe('')
    expect(joinWords(['a'])).toBe('a')
    expect(joinWords(['a', 'b'])).toBe('a and b')
    expect(joinWords(['a', 'b', 'c'])).toBe('a, b and c')
  })
})

describe('missingInputs', () => {
  it('names every documented check in plain words', () => {
    expect(
      missingInputs([
        'coverage_168h',
        'mean_168h',
        'lag_14d',
        'lag_2d',
        'cutoff_hour_missing',
        'mean_24h',
        'lag_1d',
        'lag_7d',
      ]),
    ).toEqual([
      'the load of the last hour before the cutoff (09:00 to 10:00 on the day before)',
      'for some hours, the load of the same hour one, two, seven and 14 days earlier',
      'the mean load of the 24 and 168 hours before the cutoff',
      'more than 10 % of the load values of the 168 hours before the cutoff',
    ])
  })

  it('uses the singular for one day only', () => {
    expect(missingInputs(['lag_1d'])).toEqual([
      'for some hours, the load of the same hour one day earlier',
    ])
    expect(missingInputs(['lag_7d'])).toEqual([
      'for some hours, the load of the same hour seven days earlier',
    ])
    expect(missingInputs(['mean_168h'])).toEqual([
      'the mean load of the 168 hours before the cutoff',
    ])
  })

  it('sums up unknown checks generically and ignores repeats', () => {
    expect(missingInputs(['weather_feed', 'weather_feed', '<b>x</b>'])).toEqual([
      'some of its input data',
    ])
    expect(missingInputs(['lag_1d', 'lag_1d', 'something_new'])).toEqual([
      'for some hours, the load of the same hour one day earlier',
      'other input data',
    ])
  })

  it('returns nothing without checks', () => {
    expect(missingInputs(null)).toEqual([])
    expect(missingInputs(undefined)).toEqual([])
    expect(missingInputs([])).toEqual([])
  })
})
