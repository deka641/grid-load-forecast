import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'

// En dash, em dash, middle dot and arrow glyphs are banned from the UI copy.
const BANNED = new RegExp(
  `[${[0x2013, 0x2014, 0xb7, 0x2794, 0x27a1].map((c) => String.fromCodePoint(c)).join('')}${String.fromCodePoint(0x2190)}-${String.fromCodePoint(0x21ff)}]`,
  'u',
)
const ROOT = resolve(import.meta.dirname, '..')

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    return statSync(path).isDirectory() ? files(path) : [path]
  })
}

const EMOJI = /\p{Extended_Pictographic}/u
const UPPERCASE = /text-transform:\s*uppercase/i

describe('copy', () => {
  it('contains no dash, middle dot or arrow glyphs', () => {
    const sources = [
      ...files(join(ROOT, 'src')).filter((f) => !f.endsWith('schema.d.ts')),
      ...files(join(ROOT, 'e2e')),
      join(ROOT, 'index.html'),
    ]
    const offending = sources.filter((file) => BANNED.test(readFileSync(file, 'utf8')))
    expect(offending).toEqual([])
  })

  it('contains no emojis and no all-caps styling', () => {
    const sources = [...files(join(ROOT, 'src')), ...files(join(ROOT, 'e2e'))].filter(
      (f) => !f.endsWith('glyphs.test.ts'),
    )
    expect(sources.filter((file) => EMOJI.test(readFileSync(file, 'utf8')))).toEqual([])
    const styles = sources.filter((file) => file.endsWith('.css'))
    expect(styles.length).toBeGreaterThan(0)
    expect(styles.filter((file) => UPPERCASE.test(readFileSync(file, 'utf8')))).toEqual([])
  })
})
