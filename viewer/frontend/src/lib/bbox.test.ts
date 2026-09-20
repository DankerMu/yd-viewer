import { describe, expect, it } from 'vitest'

import { boundaryBbox } from './bbox'

const EXPECTED: [[number, number], [number, number]] = [
  [100, 30],
  [101, 31],
]

describe('boundary bounding box', () => {
  it('fits a Polygon to [[100,30],[101,31]]', () => {
    expect(
      boundaryBbox({
        type: 'Feature',
        properties: {},
        geometry: {
          type: 'Polygon',
          coordinates: [
            [
              [100, 30],
              [101, 30],
              [101, 31],
              [100, 31],
              [100, 30],
            ],
          ],
        },
      }),
    ).toEqual(EXPECTED)
  })

  it('fits a MultiPolygon whose parts jointly span [[100,30],[101,31]]', () => {
    expect(
      boundaryBbox({
        type: 'Feature',
        geometry: {
          type: 'MultiPolygon',
          coordinates: [
            [
              [
                [100, 30],
                [100.2, 30],
                [100.2, 30.2],
                [100, 30],
              ],
            ],
            [
              [
                [100.8, 30.8],
                [101, 30.8],
                [101, 31],
                [100.8, 31],
                [100.8, 30.8],
              ],
            ],
          ],
        },
      }),
    ).toEqual(EXPECTED)
  })

  it('visits every vertex without spreading coordinates onto the stack', () => {
    const ring: number[][] = []
    ring.push([100, 30])
    for (let i = 0; i < 80_000; i += 1) {
      ring.push([100.5, 30.5])
    }
    ring.push([101, 31])
    ring.push([100, 30])
    expect(
      boundaryBbox({
        type: 'Feature',
        geometry: { type: 'Polygon', coordinates: [ring] },
      }),
    ).toEqual(EXPECTED)
  })
})
