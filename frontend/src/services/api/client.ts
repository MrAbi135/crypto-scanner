// Typed client for the S10a read subset.
//
// Hand-written rather than generated: the implemented surface is four rows, and
// a generator here would produce a client for endpoints that do not exist yet
// (§15 marks most of the spec `DESIGNED`). When S11 implements the full
// contract, generation from the emitted OpenAPI replaces this file wholesale --
// which is why the shapes live in `entities` and not inline.

import { env } from '@shared/config/env'
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
    headers: { Accept: 'application/json' },
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
