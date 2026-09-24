import logoUrl from '../assets/brand/logo.png'
import sponsorsUrl from '../assets/brand/sponsors.png'

export function Header() {
  return (
    <header className="flex h-[84px] shrink-0 items-center justify-between gap-4 bg-gradient-to-r from-primary-900 via-primary-800 to-primary-700 px-5 shadow-md">
      <div className="flex items-center gap-3">
        <img
          src={logoUrl}
          alt="永登流域水文模拟系统徽标"
          className="h-12 w-12 rounded-full"
          draggable={false}
        />
        <div className="leading-tight">
          <div className="text-[28px] font-extrabold tracking-wide text-white">
            永登流域水文模拟系统
          </div>
          <div className="text-[11px] uppercase tracking-[0.25em] text-primary-100/80">
            Yongdeng Basin Hydrological Modeling
          </div>
        </div>
      </div>
      <img
        src={sponsorsUrl}
        alt="合作单位"
        className="hidden h-14 object-contain lg:block"
        draggable={false}
      />
    </header>
  )
}
