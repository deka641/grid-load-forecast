import { createReadStream, existsSync, statSync } from 'node:fs'
import { createServer } from 'node:http'
import type { Server } from 'node:http'
import { extname, join, normalize, resolve } from 'node:path'

/** The policy Caddy sends in production (docker/Caddyfile). */
export const CSP =
  "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; " +
  "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; " +
  "frame-ancestors 'none'; form-action 'none'"

const TYPES: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.woff2': 'font/woff2',
  '.woff': 'font/woff',
  '.svg': 'image/svg+xml',
  '.json': 'application/json',
}

/** Serve the production build with the production CSP. API calls are mocked in the page. */
export function serveDist(root: string, port: number): Promise<Server> {
  const base = resolve(root)
  const server = createServer((request, response) => {
    const path = normalize(decodeURIComponent((request.url ?? '/').split('?')[0] ?? '/'))
    let file = join(base, path)
    if (!file.startsWith(base) || path.startsWith('/api/')) {
      response.writeHead(404).end()
      return
    }
    if (!existsSync(file) || statSync(file).isDirectory()) {
      file = join(base, 'index.html')
    }
    response.writeHead(200, {
      'Content-Type': TYPES[extname(file)] ?? 'application/octet-stream',
      'Content-Security-Policy': CSP,
      'X-Content-Type-Options': 'nosniff',
    })
    createReadStream(file).pipe(response)
  })
  return new Promise((done) => {
    server.listen(port, '127.0.0.1', () => {
      done(server)
    })
  })
}
