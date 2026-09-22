import { formatBeijingTime } from '../lib/time'

export function Header({ cycle }: { cycle: string | null }) {
  const time = cycle === null ? '暂无数据' : formatBeijingTime(cycle)
  return (
    <div
      className="pointer-events-none absolute left-1/2 top-3 z-[110] -translate-x-1/2 rounded-lg border border-white/40 bg-white/70 px-3 py-1.5 text-xs text-neutral-800 shadow-lg backdrop-blur-md supports-[backdrop-filter]:bg-white/55"
      role="banner"
    >
      <div className="font-medium">永登流域水文模拟系统</div>
      <div>流量 (m³/s)</div>
      <div>
        起报 {time} 北京时间
      </div>
    </div>
  )
}
