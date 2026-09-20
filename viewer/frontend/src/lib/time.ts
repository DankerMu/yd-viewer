const BEIJING_OFFSET_MS = 8 * 60 * 60 * 1000

export function formatBeijingTime(cycle: string, leadHours = 0): string {
  const year = Number(cycle.slice(0, 4))
  const month = Number(cycle.slice(4, 6))
  const day = Number(cycle.slice(6, 8))
  const hour = Number(cycle.slice(8, 10))
  const beijing = new Date(
    Date.UTC(year, month - 1, day, hour + leadHours) + BEIJING_OFFSET_MS,
  )
  const yyyy = String(beijing.getUTCFullYear()).padStart(4, '0')
  const mm = String(beijing.getUTCMonth() + 1).padStart(2, '0')
  const dd = String(beijing.getUTCDate()).padStart(2, '0')
  const hh = String(beijing.getUTCHours()).padStart(2, '0')
  const min = String(beijing.getUTCMinutes()).padStart(2, '0')
  return `${yyyy}-${mm}-${dd} ${hh}:${min}`
}
