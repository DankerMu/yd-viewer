import { describe, expect, it } from 'vitest'

import { formatBeijingTime } from './time'

describe('Beijing time from UTC cycle and lead', () => {
  it('formats a 12Z cycle as Beijing wall time', () => {
    expect(formatBeijingTime('2026082712')).toBe('2026-08-27 20:00')
  })

  it('adds lead hours onto a 00Z cycle', () => {
    expect(formatBeijingTime('2026082700', 5)).toBe('2026-08-27 13:00')
  })

  it('rolls over to the next Beijing calendar day', () => {
    expect(formatBeijingTime('2026082712', 5)).toBe('2026-08-28 01:00')
  })
})
