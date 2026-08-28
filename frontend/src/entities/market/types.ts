// Wire shapes from the S10a read API (API Spec §18.5, §18.7).
//
// Every price is a `string`, not a `number`, and stays one all the way to the
// screen. The API sends canonical decimal strings precisely so a price never
// passes through IEEE-754 (API §5); parsing one into a JS number here would
// undo that at the last step. Numbers appear only in `chart/scale`, where the
// value being computed is a pixel coordinate rather than a price.

export interface Candle {
  readonly open_time: string
  readonly close_time: string
  readonly open: string
  readonly high: string
  readonly low: string
  readonly close: string
  readonly volume: string
  readonly trade_count: number
  readonly source: string
}

export interface StructureEvent {
  readonly event_type: string
  readonly event_at: string
  readonly algo_version: string
  readonly evidence: unknown
}

export interface Zone {
  readonly zone_id: string
  readonly zone_type: string
  readonly polarity: string
  readonly state: string
  readonly grade: string
  readonly band_low: string
  readonly band_high: string
  readonly refined_low: string | null
  readonly refined_high: string | null
  readonly created_at: string
  readonly created_index: number
  readonly confirmed_index: number
  readonly parent_zone_id: string | null
  readonly stale_context: boolean
  readonly gap_adjacent: boolean
  readonly updated_at: string
  readonly evidence: unknown
}

export interface PoolStrength {
  readonly score: string
  readonly components: unknown
}

export interface Pool {
  readonly pool_id: string
  readonly side: string
  readonly liquidity_class: string
  readonly source: string
  readonly price: string
  readonly band_low: string
  readonly band_high: string
  readonly state: string
  readonly member_count: number
  readonly created_index: number
  readonly strength: PoolStrength
}

export interface Sweep {
  readonly pool_id: string
  readonly from_state: string
  readonly to_state: string
  readonly reason: string
  readonly transitioned_at: string
  readonly candle_index: number
  readonly evidence: unknown
}

export interface LiquidityMap {
  readonly pools: readonly Pool[]
  readonly sweeps: readonly Sweep[]
}

export interface TargetBand {
  readonly low: string
  readonly high: string
  readonly pool_id: string
  readonly strength: string
}

/** One row of §18.4's live feed — the `summary` projection, ranked. */
export interface FeedRow {
  readonly rank: number
  readonly signal_id: string
  readonly symbol: string
  readonly timeframe: string
  readonly direction: string
  readonly archetype: string
  readonly grade: string
  /** §9.1's recorded score. Does not move. */
  readonly confidence: string
  /** §9.3's decayed score. Moves with age. */
  readonly display_rank: string
  readonly age_candles: number
  readonly entry: { readonly proximal: string; readonly distal: string }
  readonly invalidation: string
  readonly targets: {
    readonly primary: TargetBand | null
    readonly secondary: TargetBand | null
  }
  readonly published_at: string
  readonly ttl_candles: number
  readonly lifecycle_state: string | null
  readonly versions: Versions
}

// §13. `freshness` is always present; `versions` only on doctrine-derived rows.
export interface Freshness {
  readonly state: string
  readonly observed_at?: string
  readonly delay_minutes?: number
}

export interface Versions {
  readonly algo_version: string
  readonly param_set_version: string
}

export interface Meta {
  readonly generated_at: string
  readonly freshness: Freshness
  readonly versions?: Versions
}

export interface Envelope<T> {
  readonly data: T
  readonly meta: Meta
  readonly page?: {
    readonly count: number
    readonly has_more: boolean
    /**
     * §18.4's denominator, before filtering. Optional because only the rows
     * that carry one send it -- and typed as such rather than defaulted to a
     * number, so a screen has to decide what it means when absent instead of
     * being handed a zero it cannot distinguish from a real one.
     */
    readonly live_total?: number
  }
}

export interface ApiError {
  readonly error: {
    readonly code: string
    readonly message: string
    readonly correlation_id: string
    readonly details?: readonly { field: string; code: string; message: string }[]
  }
}
