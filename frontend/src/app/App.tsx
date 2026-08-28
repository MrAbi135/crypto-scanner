// Application shell.
//
// **The URL is the state.** Which screen is open, which context the chart is
// on, and which object's evidence is showing all live in the address bar; the
// shell reads them from there and writes them back. Nothing is duplicated in a
// `useState` beside them, because two copies of "where am I" is how a back
// button comes to look like it works and does nothing.
//
// It was a switcher first, with no URLs at all. That was honest for one sprint
// and stopped being so the moment there was something worth sending someone --
// S15's DoD asks for an evidence deep-link, and a selection kept in component
// state cannot be sent to anybody.
//
// The screens are still panels swapped in place rather than a route tree; the
// grammar is four screens and two segments, and `@shared/lib/route` is the
// whole of it.

import { useCallback } from 'react'

import { ChartScreen } from '@features/chart/ChartScreen'
import { RankingsScreen } from '@features/scanner/RankingsScreen'
import { DashboardScreen } from '@features/dashboard/DashboardScreen'
import { HistoryScreen } from '@features/history/HistoryScreen'
import { SignalScreen } from '@features/signal/SignalScreen'
import { ScannerScreen } from '@features/scanner/ScannerScreen'
import { UniverseScreen } from '@features/scanner/UniverseScreen'
import { CommandPalette } from '@features/palette/CommandPalette'
import { StatusStrip } from '@features/status/StatusStrip'
import type { ViewId } from '@shared/lib/route'
import { useRoute } from '@shared/lib/useRoute'

import './app.css'

const VIEWS = [
  { id: 'feed', label: 'Live feed' },
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'rankings', label: 'Rankings' },
  { id: 'chart', label: 'Chart' },
  { id: 'universe', label: 'Universe' },
  { id: 'history', label: 'Track record' },
] as const

export function App() {
  const [route, navigate] = useRoute()

  const view = route.view

  function openChart(symbol: string, timeframe: string, object?: string) {
    navigate({
      view: 'chart',
      symbol,
      timeframe,
      ...(object === undefined ? {} : { object }),
    })
  }

  function isView(id: string): id is ViewId {
    return VIEWS.some((candidate) => candidate.id === id)
  }

  // Stable, because the chart reports its context from an effect: a new
  // function identity on every render of the shell would re-fire that effect
  // on every render of the chart.
  const onChartContext = useCallback(
    (next: { symbol: string; timeframe: string; object: string | null }) => {
      navigate({
        view: 'chart',
        symbol: next.symbol,
        timeframe: next.timeframe,
        ...(next.object === null ? {} : { object: next.object }),
      })
    },
    [navigate],
  )

  const chartContext =
    route.symbol === undefined || route.timeframe === undefined
      ? undefined
      : {
          symbol: route.symbol,
          timeframe: route.timeframe,
          ...(route.object === undefined ? {} : { object: route.object }),
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
          if (isView(id)) navigate({ view: id })
        }}
        onSymbol={(symbol) => openChart(symbol, 'H1')}
      />

      {/* Still `tablist` rather than a nav of links, and now for a narrower
          reason than before: these are panels swapped in place. The back button
          works, because the address changes with them -- but a `tab` is what a
          reader is operating, and calling it a link would misdescribe the
          keyboard behaviour they get. */}
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
            onClick={() => navigate({ view: id })}
          >
            {label}
          </button>
        ))}
      </div>

      <div id={`panel-${view}`} role="tabpanel" aria-labelledby={`tab-${view}`}>
        {view === 'feed' && (
          <ScannerScreen
            onShowFloors={() => navigate({ view: 'rankings' })}
            onOpenChart={openChart}
            onOpenSignal={(signalId) => navigate({ view: 'signal', signalId })}
          />
        )}
        {view === 'rankings' && <RankingsScreen />}
        {/* `openOn` is applied on mount and whenever it changes -- which is
            what makes the back button real: a history entry arriving from
            outside is a change, and the chart re-seeds from it. The chart
            reports its own context straight back, so a symbol typed into its
            box lands in the address bar and does not bounce. */}
        {view === 'chart' && <ChartScreen openOn={chartContext} onContext={onChartContext} />}
        {view === 'universe' && <UniverseScreen />}
        {view === 'dashboard' && (
          <DashboardScreen
            onOpenSignal={(signalId) => navigate({ view: 'signal', signalId })}
            onOpenChart={openChart}
          />
        )}
        {view === 'history' && (
          <HistoryScreen onOpenSignal={(signalId) => navigate({ view: 'signal', signalId })} />
        )}
        {/* Not a tab: a detail screen reached from a row or a link. While it
            is open no tab reads selected, which is true -- the reader is not
            on any of the four boards. */}
        {view === 'signal' && route.signalId !== undefined && (
          <SignalScreen signalId={route.signalId} onOpenChart={openChart} />
        )}
      </div>
    </main>
  )
}
