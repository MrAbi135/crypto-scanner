// The application's URL grammar, as a pure pair of functions.
//
// **Why there is no router library here.** The whole grammar is four screens
// and two optional segments; a router would bring a dependency, a component
// tree and a matching engine to express what fits in one `switch`. If the
// grammar grows past this -- nested layouts, loaders, guards -- that is the
// moment to take the dependency, not before.
//
// Parsing and printing are one file and are tested against each other, because
// the failure that matters is not a wrong parse: it is a parse and a print
// that disagree, so the address bar says one thing and the screen shows
// another. That is worse than no routing at all.

export type ViewId = 'feed' | 'dashboard' | 'rankings' | 'chart' | 'universe' | 'history' | 'signal'

export interface Route {
  readonly view: ViewId
  /** Chart only. */
  readonly symbol?: string
  /** Chart only. */
  readonly timeframe?: string
  /**
   * Chart only: the object whose evidence is open.
   *
   * In the URL because that is what makes an evidence link shareable -- S15's
   * "evidence deep-link → highlight". A selection kept only in component state
   * cannot be sent to anyone.
   */
  readonly object?: string
  /** Signal only: the published signal on screen. Same argument as `object`:
   *  a conviction surface nobody can link to cannot be argued about. */
  readonly signalId?: string
}

const VIEWS: readonly ViewId[] = ['feed', 'dashboard', 'rankings', 'chart', 'universe', 'history', 'signal']

/** The address for a route. Always absolute, always starts with `/`. */
export function toPath(route: Route): string {
  if (route.view === 'signal') {
    // A signal id is opaque server output, so it is escaped like the symbol
    // below -- and a signal route with no id is just the feed, because there
    // is no "signal screen about nothing" to show.
    return route.signalId === undefined || route.signalId === ''
      ? '/'
      : `/signal/${encodeURIComponent(route.signalId)}`
  }

  if (route.view !== 'chart') {
    // The feed is the root, not `/feed`. It is where an unadorned visit lands
    // and it should not immediately rewrite the address bar to say so.
    return route.view === 'feed' ? '/' : `/${route.view}`
  }

  const symbol = route.symbol ?? ''
  const timeframe = route.timeframe ?? ''

  // A chart with no context is still the chart screen; it opens on its own
  // default rather than 404ing on an address a reader typed halfway.
  if (symbol === '') return '/chart'
  if (timeframe === '') return `/chart/${encodeURIComponent(symbol)}`

  const base = `/chart/${encodeURIComponent(symbol)}/${encodeURIComponent(timeframe)}`

  return route.object === undefined || route.object === ''
    ? base
    : `${base}?object=${encodeURIComponent(route.object)}`
}

/** The route for an address. Never throws; an unknown address is the feed. */
export function fromPath(path: string, query = ''): Route {
  const parts = path.split('/').filter((part) => part !== '')

  if (parts.length === 0) return { view: 'feed' }

  const [head, ...rest] = parts

  if (head === 'signal') {
    const [signalId] = rest.map(decode)

    return signalId === undefined || signalId === ''
      ? { view: 'feed' }
      : { view: 'signal', signalId }
  }

  if (head === 'chart') {
    const [symbol, timeframe] = rest.map(decode)
    const object = new URLSearchParams(query).get('object')

    return {
      view: 'chart',
      ...(symbol === undefined ? {} : { symbol }),
      ...(timeframe === undefined ? {} : { timeframe }),
      ...(object === null || object === '' ? {} : { object }),
    }
  }

  // An address nobody serves lands on the feed rather than on a blank screen.
  // A 404 view would be a fifth screen to build and maintain for typos.
  return { view: (VIEWS as readonly string[]).includes(head!) ? (head as ViewId) : 'feed' }
}

function decode(part: string): string {
  try {
    return decodeURIComponent(part)
  } catch {
    // A stray `%` in a hand-typed address is not worth an exception that takes
    // the shell down; the raw segment is a better guess than a crash.
    return part
  }
}
