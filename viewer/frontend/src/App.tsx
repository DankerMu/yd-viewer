import { useState } from 'react'

import { Header } from './components/Header'
import { RiverCurveWindow } from './components/map/RiverCurveWindow'
import type { MapLatestResponse } from './lib/api'
import { MapPage } from './pages/MapPage'

export function App() {
  const [latest, setLatest] = useState<MapLatestResponse | null>(null)
  const [selectedReach, setSelectedReach] = useState<number | null>(null)

  return (
    <div className="relative h-screen w-screen overflow-hidden">
      <MapPage onLatestChange={setLatest} onReachSelect={setSelectedReach} />
      <Header cycle={latest?.cycle ?? null} />
      <RiverCurveWindow
        reachId={selectedReach}
        mapCycle={latest?.cycle ?? null}
        onClose={() => setSelectedReach(null)}
      />
    </div>
  )
}
