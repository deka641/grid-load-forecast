import type { Status } from '../api/client'
import { dateTimeBerlin } from '../lib/time'

export const SOURCE_URL = 'https://github.com/deka641/grid-load-forecast'

function PipelineRun({ status }: { status: Status }) {
  const run = status.last_load_run
  if (!run) {
    return <li>Last pipeline run: none yet</li>
  }
  const when = run.finished_at ?? run.started_at
  const verb = run.finished_at ? 'finished' : 'started'
  return (
    <li>
      Last pipeline run: {run.status} ({run.mode}), {verb} {dateTimeBerlin(when)}
    </li>
  )
}

export function Footer({ status }: { status: Status | undefined }) {
  return (
    <footer className="site-footer">
      <p>
        Data: <a href="https://www.smard.de">Bundesnetzagentur | SMARD.de</a>, licensed under{' '}
        <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>
      </p>
      <ul className="footer-meta">
        {status ? <PipelineRun status={status} /> : null}
        {status?.snapshot_created_at ? (
          <li>Snapshot: {dateTimeBerlin(status.snapshot_created_at)}</li>
        ) : null}
        <li>
          <a href={SOURCE_URL}>Source code</a>
        </li>
      </ul>
    </footer>
  )
}
