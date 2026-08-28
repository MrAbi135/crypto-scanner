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
  LiquidityMap,
  StructureEvent,
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
