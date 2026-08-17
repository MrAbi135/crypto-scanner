// Application shell. Hosts the single S13a screen; routing arrives with S13.
import { ChartScreen } from '@features/chart/ChartScreen'

export function App() {
  return (
    <main>
      <h1>Institutional AI Crypto Scanner</h1>
      <ChartScreen />
    </main>
  )
}
