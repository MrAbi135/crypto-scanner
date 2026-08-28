// The browser half of the URL grammar: read the address, change it, and hear
// about the back button.
//
// Split from `route.ts` so the grammar itself stays pure and testable without a
// DOM, and so the one piece that touches `history` is small enough to read in
// full.

import { useCallback, useEffect, useState } from 'react'

import { fromPath, toPath, type Route } from '@shared/lib/route'

export function useRoute(): [Route, (route: Route) => void] {
  const [route, setRoute] = useState<Route>(() => read())

  useEffect(() => {
    // The back button is the entire reason the address bar is worth writing to.
    // Without this, `pushState` would fill the history stack with entries that
    // do nothing when the reader goes back to them -- which is worse than
    // having no history at all, because the button looks like it works.
    function onPop() {
      setRoute(read())
    }

    window.addEventListener('popstate', onPop)

    return () => window.removeEventListener('popstate', onPop)
  }, [])

  const navigate = useCallback((next: Route) => {
    const path = toPath(next)

    // Replace rather than push when the address does not change, so clicking
    // the tab you are already on does not stack an identical entry that the
    // back button then has to be pressed twice to get past.
    if (path === window.location.pathname + window.location.search) {
      window.history.replaceState(null, '', path)
    } else {
      window.history.pushState(null, '', path)
    }

    setRoute(next)
  }, [])

  return [route, navigate]
}

function read(): Route {
  return fromPath(window.location.pathname, window.location.search)
}
