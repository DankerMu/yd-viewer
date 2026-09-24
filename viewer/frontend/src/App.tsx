import { useState } from 'react'

import { Header } from './components/Header'
import { RiverCurveWindow } from './components/map/RiverCurveWindow'
import type { MapLatestResponse } from './lib/api'
import { MapPage } from './pages/MapPage'

export function App() {
  const [latest, setLatest] = useState<MapLatestResponse | null>(null)
  const [selectedReach, setSelectedReach] = useState<number | null>(null)

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden">
      <Header />
      <section className="relative min-h-0 flex-1 overflow-hidden bg-[#d7e7ef]">
        <MapPage onLatestChange={setLatest} onReachSelect={setSelectedReach} />
        <RiverCurveWindow
          reachId={selectedReach}
          mapCycle={latest?.cycle ?? null}
          onClose={() => setSelectedReach(null)}
        />
      </section>
    </div>
  )
}
