import { describeRun } from './format'

describe('describeRun', () => {
  it('puts the pipeline run status and mode into words', () => {
    expect(describeRun('ok', 'incremental')).toBe('succeeded, incremental run')
    expect(describeRun('partial', 'backfill')).toBe('partly succeeded, backfill run')
    expect(describeRun('failed', 'incremental')).toBe('failed, incremental run')
    expect(describeRun('aborted', 'incremental')).toBe('aborted, incremental run')
    expect(describeRun('running', 'backfill')).toBe('running, backfill run')
  })

  it('shows unknown values as they are', () => {
    expect(describeRun('queued', 'replay')).toBe('queued, replay')
  })
})
