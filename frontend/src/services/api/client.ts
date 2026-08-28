// Typed client for the S10a read subset.
//
// Hand-written rather than generated: the implemented surface is four rows, and
// a generator here would produce a client for endpoints that do not exist yet
// (§15 marks most of the spec `DESIGNED`). When S11 implements the full
// contract, generation from the emitted OpenAPI replaces this file wholesale --
// which is why the shapes live in `entities` and not inline.

import { env } from '@shared/config/env'
import { currentSession, setSession, type Session } from '@services/api/session'
import type {
  ApiError,
  Candle,
  Envelope,
  FeedRow,
  LiquidityMap,
  PlatformStatus,
  RankedRow,
  SignalDetail,
  SignalEvidence,
  SignalTransition,
  StructureEvent,
  UniverseSymbol,
  Weights,
  Zone,
} from '@entities/market/types'

export class ApiRequestError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly correlationId: string,
  ) {
    super(message)
    this.name = 'ApiRequestError'
  }
}

async function request<T>(path: string, params: Record<string, string>): Promise<Envelope<T>> {
  const query = new URLSearchParams(params).toString()

  const response = await fetch(`${env.apiBase}/v1${path}?${query}`, {
    headers: authorised({ Accept: 'application/json' }),
    // The refresh cookie is HttpOnly and same-origin; without this the browser
    // withholds it on a cross-origin dev setup and the session cannot renew.
    credentials: 'include',
  })

  const body: unknown = await response.json()

  if (!response.ok) {
    // The correlation id is carried through so a user-reported problem can be
    // found in the logs by the same id they were shown -- the whole reason §7
    // puts one in every error.
    const failure = body as ApiError

    throw new ApiRequestError(
      failure.error?.code ?? 'INTERNAL',
      failure.error?.message ?? 'Request failed.',
      failure.error?.correlation_id ?? 'unknown',
    )
  }

  return body as Envelope<T>
}

export function fetchFeed(
  filters: Record<string, string> = {},
): Promise<Envelope<readonly FeedRow[]>> {
  // Filters go to the server and nothing narrows the rows after they arrive.
  // §9 calls a filter the server did not apply "a lie the client believes",
  // and `page.live_total` is only an honest denominator because the server
  // reports what it filtered out.
  //
  // Still no sort: §18.4 fixes it and §9.2 is a total order, so there is
  // nothing here for a caller to reorder.
  return request<readonly FeedRow[]>('/scanner/feed', filters)
}

export function fetchUniverse(
  filters: Record<string, string> = {},
): Promise<Envelope<readonly UniverseSymbol[]>> {
  // Filters go to the server, as everywhere: §9 refuses one it cannot apply
  // rather than letting a client narrow a list it was already sent.
  return request<readonly UniverseSymbol[]>('/scanner/universe', filters)
}

export function fetchSignalDetail(signalId: string): Promise<Envelope<SignalDetail>> {
  // Always `full`: the provenance footer needs the hash and the server's own
  // verification of it, and a second request to upgrade the projection would
  // double the reads on the screen §15.4 calls the conviction surface.
  return request<SignalDetail>(`/signals/${encodeURIComponent(signalId)}`, {
    projection: 'full',
  })
}

export function fetchSignalEvidence(signalId: string): Promise<Envelope<SignalEvidence>> {
  return request<SignalEvidence>(`/signals/${encodeURIComponent(signalId)}/evidence`, {})
}

export function fetchSignalTransitions(
  signalId: string,
): Promise<Envelope<readonly SignalTransition[]>> {
  return request<readonly SignalTransition[]>(
    `/signals/${encodeURIComponent(signalId)}/transitions`,
    {},
  )
}

export function fetchStatus(): Promise<Envelope<PlatformStatus>> {
  // No filters and no paging: §18.3's strip is the whole platform's state, and
  // a paged one would report "0 behind" for the page it happened to fetch.
  return request<PlatformStatus>('/dashboard/status', {})
}

export function fetchWeights(): Promise<Envelope<Weights>> {
  return request<Weights>('/rankings/weights', {})
}

export function fetchRankings(
  symbols: readonly string[],
  timeframe: string,
): Promise<Envelope<readonly RankedRow[]>> {
  // No `at`. §18.6 floors the moment to the last close on the server, and a
  // client passing its own clock would ask for a board between closes and be
  // told, correctly, that nothing was ranked there.
  return request<readonly RankedRow[]>('/rankings', {
    symbols: symbols.join(','),
    timeframe,
  })
}

export function fetchCandles(
  symbolId: string,
  timeframe: string,
  limit = 300,
): Promise<Envelope<readonly Candle[]>> {
  return request('/market/candles', {
    symbol_id: symbolId,
    timeframe,
    limit: String(limit),
  })
}

export function fetchStructure(
  symbolId: string,
  timeframe: string,
): Promise<Envelope<readonly StructureEvent[]>> {
  return request(`/coins/${encodeURIComponent(symbolId)}/structure`, { timeframe })
}

export function fetchZones(
  symbolId: string,
  timeframe: string,
): Promise<Envelope<readonly Zone[]>> {
  return request(`/coins/${encodeURIComponent(symbolId)}/zones`, { timeframe })
}

export function fetchLiquidity(
  symbolId: string,
  timeframe: string,
): Promise<Envelope<LiquidityMap>> {
  return request(`/coins/${encodeURIComponent(symbolId)}/liquidity`, { timeframe })
}


/**
 * Add the bearer token, if there is one.
 *
 * A missing token is not an error here. Every read row answers 401 on its own
 * and the screen renders that as what it is; short-circuiting in the client
 * would replace the server's own answer -- correlation id and all -- with a
 * guess made before asking.
 */
function authorised(headers: Record<string, string>): Record<string, string> {
  const session = currentSession()

  return session === null
    ? headers
    : { ...headers, Authorization: `Bearer ${session.accessToken}` }
}

interface TokenResponse {
  readonly access_token: string
  readonly token_type: string
  readonly expires_in: number
  readonly user_id: string
  readonly tenant_id: string
}

/**
 * §18.1 login. Stores the session and returns it.
 *
 * `expires_in` is turned into an absolute instant here, once, against this
 * machine's clock. Keeping the duration and recomputing later would restart
 * the countdown on every read.
 */
export async function login(email: string, password: string): Promise<Session> {
  const response = await fetch(`${env.apiBase}/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ email, password }),
  })

  const body: unknown = await response.json()

  if (!response.ok) {
    const failure = body as ApiError

    throw new ApiRequestError(
      failure.error?.code ?? 'INTERNAL',
      // §18.1 answers one code for wrong password, unknown address, disabled
      // and deleted, deliberately. Passing the server's message through keeps
      // it that way rather than inventing a more specific one here.
      failure.error?.message ?? 'Sign-in failed.',
      failure.error?.correlation_id ?? 'unknown',
    )
  }

  const token = body as TokenResponse

  const session: Session = {
    accessToken: token.access_token,
    userId: token.user_id,
    tenantId: token.tenant_id,
    expiresAt: Date.now() + token.expires_in * 1000,
  }

  setSession(session)

  return session
}

/** §18.1 logout. Clears the session whatever the server says. */
export async function logout(): Promise<void> {
  try {
    await fetch(`${env.apiBase}/v1/auth/logout`, {
      method: 'POST',
      headers: authorised({ Accept: 'application/json' }),
      credentials: 'include',
    })
  } finally {
    // Local first, unconditionally. A logout that leaves the token in memory
    // because the network was down is the failure that matters.
    setSession(null)
  }
}
