export type ForecastSource = 'gfs' | 'ifs'

export type CycleEntry = {
  cycle: string
  sources: ForecastSource[]
}

export type MapLatestResponse = {
  cycle: string
  source: ForecastSource
  valid_time: string
  values: Array<number | null>
}

export type CurveResponse = {
  cycle: string
  reach_id: number
  lead_hours: number[]
  series: {
    gfs?: Array<number | null>
    ifs?: Array<number | null>
  }
}

export function resolveUrl(pageUrl: string, relativePath: string): string {
  return new URL(relativePath, pageUrl).href
}

export function cyclesUrl(pageUrl: string): string {
  return resolveUrl(pageUrl, 'api/cycles')
}

export function mapLatestUrl(pageUrl: string): string {
  return resolveUrl(pageUrl, 'api/map/latest')
}

export function curveUrl(pageUrl: string, cycle: string, reachId: number): string {
  return resolveUrl(pageUrl, `api/cycles/${cycle}/reaches/${reachId}`)
}
