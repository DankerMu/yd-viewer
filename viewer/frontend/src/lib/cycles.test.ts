import { describe, expect, it } from 'vitest'

import { cycleOptions } from './cycles'

describe('cycle dropdown options', () => {
  it('labels cycles in Beijing time and keeps API order', () => {
    expect(
      cycleOptions([
        { cycle: '2026082712', sources: ['gfs', 'ifs'] },
        { cycle: '2026082700', sources: ['gfs'] },
      ]),
    ).toEqual([
      { cycle: '2026082712', label: '2026-08-27 20:00' },
      { cycle: '2026082700', label: '2026-08-27 08:00' },
    ])
  })

  it('does not reorder a non-descending cycles list', () => {
    expect(
      cycleOptions([
        { cycle: '2026082700', sources: ['ifs'] },
        { cycle: '2026082712', sources: ['gfs'] },
      ]).map((option) => option.cycle),
    ).toEqual(['2026082700', '2026082712'])
  })

  it('returns no options when cycles are empty', () => {
    expect(cycleOptions([])).toEqual([])
  })
})
