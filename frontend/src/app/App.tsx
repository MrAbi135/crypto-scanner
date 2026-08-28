// Application shell.
//
// **A switcher, not a router.** There are no URLs, no history and no deep
// links; those are S13's and this does not pretend to be them. It exists
// because S14 added two screens that nothing could reach, and a screen nobody
// can open is indistinguishable from one that was never written — which is
// exactly how S13a's chart came to be built, tested and merged while being
// invisible on the host for a fortnight.
//
// The whole of the navigation contract here is: three named views, one at a
// time, and the quiet feed's "See the floors" lands on the right one.

import { useState } from 'react'

import { ChartScreen } from '@features/chart/ChartScreen'
import { RankingsScreen } from '@features/scanner/RankingsScreen'
import { ScannerScreen } from '@features/scanner/ScannerScreen'

import './app.css'

const VIEWS = [
  { id: 'feed', label: 'Live feed' },
  { id: 'rankings', label: 'Rankings' },
  { id: 'chart', label: 'Chart' },
] as const

type ViewId = (typeof VIEWS)[number]['id']

export function App() {
  const [view, setView] = useState<ViewId>('feed')

  return (
    <main>
      <h1 className="app__title">Institutional AI Crypto Scanner</h1>

      {/* `tablist` and not a nav of links: these are panels swapped in place,
          and calling them links would promise a back button that does not
          exist. */}
      <div className="app__views" role="tablist" aria-label="Views">
        {VIEWS.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            role="tab"
            id={`tab-${id}`}
            aria-selected={view === id}
            aria-controls={`panel-${id}`}
            className={`app__view${view === id ? ' app__view--on' : ''}`}
            onClick={() => setView(id)}
          >
            {label}
          </button>
        ))}
      </div>

      <div id={`panel-${view}`} role="tabpanel" aria-labelledby={`tab-${view}`}>
        {view === 'feed' && <ScannerScreen onShowFloors={() => setView('rankings')} />}
        {view === 'rankings' && <RankingsScreen />}
        {view === 'chart' && <ChartScreen />}
      </div>
    </main>
  )
}
