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
import { UniverseScreen } from '@features/scanner/UniverseScreen'
import { CommandPalette } from '@features/palette/CommandPalette'
import { StatusStrip } from '@features/status/StatusStrip'

import './app.css'

const VIEWS = [
  { id: 'feed', label: 'Live feed' },
  { id: 'rankings', label: 'Rankings' },
  { id: 'chart', label: 'Chart' },
  { id: 'universe', label: 'Universe' },
] as const

type ViewId = (typeof VIEWS)[number]['id']

export function App() {
  const [view, setView] = useState<ViewId>('feed')
  const [chart, setChart] = useState<{ symbol: string; timeframe: string } | null>(null)

  function openChart(symbol: string, timeframe: string) {
    setChart({ symbol, timeframe })
    setView('chart')
  }

  function isView(id: string): id is ViewId {
    return VIEWS.some((view) => view.id === id)
  }

  return (
    <main>
      <h1 className="app__title">Institutional AI Crypto Scanner</h1>

      {/* Above the views and outside the switcher: whether the platform is
          covered is not a property of the panel you happen to be looking at,
          and a board of stale rows looks identical to a board of fresh ones. */}
      <StatusStrip />

      {/* Chrome, not a screen: it is reachable from every view and renders
          nothing until someone presses the shortcut. A symbol chosen here opens
          on H1 -- the palette has no timeframe to offer and the chart's own
          selector is one control away, which beats four rows per symbol. */}
      <CommandPalette
        views={VIEWS}
        onScreen={(id) => {
          if (isView(id)) setView(id)
        }}
        onSymbol={(symbol) => openChart(symbol, 'H1')}
      />

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
        {view === 'feed' && (
          <ScannerScreen onShowFloors={() => setView('rankings')} onOpenChart={openChart} />
        )}
        {view === 'rankings' && <RankingsScreen />}
        {/* The panels are unmounted when they are not shown, and that is what
            makes `openOn` enough on its own: every arrival at the chart is a
            fresh mount, so the requested context is read afresh each time.
            A request nonce and an explicit `key` were written here first and
            removed -- two mutations to them changed no test, because nothing
            could reach the chart without remounting it anyway. If the chart
            ever stays mounted while the feed is visible, this is the line that
            has to change. */}
        {view === 'chart' && <ChartScreen openOn={chart ?? undefined} />}
        {view === 'universe' && <UniverseScreen />}
      </div>
    </main>
  )
}
