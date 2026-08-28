import { describe, expect, it } from 'vitest'

import { fromPath, toPath, type Route } from '@shared/lib/route'

const ROUTES: readonly Route[] = [
  { view: 'feed' },
  { view: 'rankings' },
  { view: 'universe' },
  { view: 'chart' },
  { view: 'chart', symbol: 'BTCUSDT' },
  { view: 'chart', symbol: 'BTCUSDT', timeframe: 'H1' },
  { view: 'chart', symbol: 'BTCUSDT', timeframe: 'H4', object: 'zone:abc-123' },
  { view: 'signal', signalId: 'sig-2026-abc' },
]

describe('route grammar', () => {
  it('treats a signal route with no id as the feed', () => {
    // There is no "signal screen about nothing" to show, in either direction.
    expect(toPath({ view: 'signal' })).toBe('/')
    expect(fromPath('/signal')).toEqual({ view: 'feed' })
  })

  it('escapes a signal id rather than letting it invent segments', () => {
    expect(toPath({ view: 'signal', signalId: 'a/b' })).toBe('/signal/a%2Fb')
    expect(fromPath('/signal/a%2Fb')).toEqual({ view: 'signal', signalId: 'a/b' })
  })


  // The failure that matters is not a wrong parse. It is a parse and a print
  // that disagree -- the address bar saying one thing while the screen shows
  // another -- so every route is checked against its own round trip rather
  // than against a hand-written expected string.
  it.each(ROUTES)('survives a round trip: %j', (route) => {
    const path = toPath(route)
    const [pathname, query] = path.split('?')

    expect(fromPath(pathname!, query ?? '')).toEqual(route)
  })

  it('puts the feed at the root rather than at /feed', () => {
    // An unadorned visit should not immediately rewrite the address bar to
    // announce where it already was.
    expect(toPath({ view: 'feed' })).toBe('/')
    expect(fromPath('/')).toEqual({ view: 'feed' })
  })

  it('lands an address nobody serves on the feed', () => {
    // A fifth screen to build and maintain for typos, versus the screen they
    // most likely wanted.
    expect(fromPath('/nowhere')).toEqual({ view: 'feed' })
    expect(fromPath('/rankings/extra/segments')).toEqual({ view: 'rankings' })
  })

  it('accepts a chart address typed halfway', () => {
    // Somebody will delete the timeframe off the end of a link. That is the
    // chart screen on its own default, not a 404.
    expect(fromPath('/chart')).toEqual({ view: 'chart' })
    expect(fromPath('/chart/ETHUSDT')).toEqual({ view: 'chart', symbol: 'ETHUSDT' })
  })

  it('carries the inspected object, because that is the shareable part', () => {
    expect(toPath({ view: 'chart', symbol: 'BTCUSDT', timeframe: 'H1', object: 'pool:p1' })).toBe(
      '/chart/BTCUSDT/H1?object=pool%3Ap1',
    )
  })

  it('does not put an object on an address that has no context to hang it on', () => {
    // `/chart?object=zone:x` would be a link to an object on no chart.
    expect(toPath({ view: 'chart', object: 'zone:x' })).toBe('/chart')
  })

  it('treats an empty object exactly as an absent one', () => {
    // `?object=` arrives from a hand-edited link and from a stale bookmark. It
    // is not a selection of something called "".
    expect(toPath({ view: 'chart', symbol: 'B', timeframe: 'H1', object: '' })).toBe('/chart/B/H1')
    expect(fromPath('/chart/B/H1', '?object=')).toEqual({
      view: 'chart',
      symbol: 'B',
      timeframe: 'H1',
    })
  })

  it('escapes a symbol rather than letting it invent a segment', () => {
    expect(toPath({ view: 'chart', symbol: 'A/B', timeframe: 'H1' })).toBe('/chart/A%2FB/H1')
    expect(fromPath('/chart/A%2FB/H1')).toEqual({ view: 'chart', symbol: 'A/B', timeframe: 'H1' })
  })

  it('does not throw on an address that cannot be decoded', () => {
    // A stray `%` in a hand-typed address must not take the shell down; the
    // raw segment is a better answer than a white screen.
    expect(() => fromPath('/chart/100%/H1')).not.toThrow()
    expect(fromPath('/chart/100%/H1').symbol).toBe('100%')
  })
})
