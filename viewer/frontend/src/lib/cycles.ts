import type { CycleEntry } from './api'
import { formatBeijingTime } from './time'

export type CycleOption = {
  cycle: string
  label: string
}

export function cycleOptions(cycles: CycleEntry[]): CycleOption[] {
  const options: CycleOption[] = []
  for (let i = 0; i < cycles.length; i += 1) {
    const cycle = cycles[i].cycle
    options.push({ cycle, label: formatBeijingTime(cycle) })
  }
  return options
}
