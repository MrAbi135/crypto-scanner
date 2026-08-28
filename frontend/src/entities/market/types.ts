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

/** §9.1's weight table, published verbatim (§18.6's "doctrine transparency"). */
export interface FactorWeight {
  readonly factor: string
  readonly name: string
  readonly weight: string
  readonly weight_pct: string
  /** §9.1's own prose. Transcribed, never paraphrased. */
  readonly justification: string
}

export interface GradeBand {
  readonly grade: string
  readonly min_confidence: string
}

export interface Weights {
  readonly param_set_version: string
  readonly factors: readonly FactorWeight[]
  readonly grades: readonly GradeBand[]
  /** §9.4: below the lowest floor is not a weak grade, it is not published. */
  readonly below_lowest_floor: string
}

/** One row of §18.6's deterministic board. */
export interface RankedRow {
  readonly rank: number
  readonly symbol: string
  readonly timeframe: string
  readonly direction: string
  readonly archetype: string
  readonly tier: string
  readonly confidence: string
  readonly display_rank: string
}

/** One row of §18.4's universe view (DDD T1/T2). */
export interface UniverseSymbol {
  readonly symbol: string
  readonly base_asset: string
  readonly quote_asset: string
  readonly status: string
  readonly tier: string
  readonly candidate_tier: string | null
  readonly consecutive_passes: number
  readonly consecutive_failures: number
  /** Daily liquidity observations. §1.4 needs seven before evaluating at all. */
  readonly observation_days: number
  /** collecting, evaluating or failing -- the server's word, not a derivation. */
  readonly assessment: string
  readonly first_seen_at: string
}

/** One stored series, as §18.3's status row sees it. */
export interface FeedCoverage {
  readonly symbol: string
  readonly timeframe: string
  /** NO_DATA, AWAITING_CLOSE or BEHIND. Not §2.12's freshness — see the API. */
  readonly coverage: string
  readonly newest_close: string
  /** Zero while merely awaiting a close. Alongside the state, not instead. */
  readonly candles_behind: number
}

/** One open ingest incident (§18.3's "degraded symbol-TFs"). */
export interface DegradedFeed {
  readonly id: string
  readonly type: string
  readonly symbol: string | null
  readonly timeframe: string | null
  readonly started_at: string
  readonly candle_span: number | null
}

/** §18.3's status strip. */
export interface PlatformStatus {
  readonly feeds: readonly FeedCoverage[]
  readonly behind_count: number
  readonly degraded: readonly DegradedFeed[]
  readonly degraded_count: number
  /**
   * What §18.3 asks for and this endpoint cannot answer, named by the server.
   * Rendered, not dropped: a strip that silently shows two of four reads as a
   * strip that checked all four and found nothing wrong.
   */
  readonly not_measured: readonly string[]
}

/** §12.4's resolved outcome, present only once the signal is settled. */
export interface SignalOutcome {
  readonly outcome: string
  readonly resolved_at: string
  readonly elapsed_candles: number
  readonly mfe_r: string
  readonly mae_r: string
}

/** §18.8's detail row (`projection=full`), which is the feed row plus the seal. */
export interface SignalDetail {
  readonly signal_id: string
  readonly symbol: string
  readonly timeframe: string
  readonly direction: string
  readonly archetype: string
  readonly grade: string
  readonly confidence: string
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
  readonly outcome?: SignalOutcome
  /** The sealed §15.2 payload, verbatim. */
  readonly payload?: Record<string, unknown>
  readonly payload_hash?: string
  /** Recomputed server-side on every read, not trusted from the column. */
  readonly payload_hash_verified?: boolean
}

/** §18.8's evidence row — the sealed chain and §15.4's breakdown. */
export interface SignalEvidence {
  readonly signal_id: string
  readonly symbol: string
  readonly timeframe: string
  readonly evidence_ids: readonly string[]
  readonly entry_zone_id: string | null
  /** §15.4: the number never travels without the factors. */
  readonly confidence: {
    readonly final: string | null
    readonly grade: string | null
    readonly factors: Record<string, string>
  }
  readonly reason: string | null
  readonly htf_chain: Record<string, string>
  readonly risk: Record<string, unknown>
}

/** One §12 lifecycle transition, stress tests included (§18.8). */
export interface SignalTransition {
  readonly from_state: string | null
  readonly to_state: string
  readonly at_candle_open_time: string
  readonly recorded_at: string
  readonly stress_test: boolean
  readonly refresh: boolean
  readonly evidence: Record<string, unknown>
}

/** One §18.8 archive row: the signal, and (once resolved) what became of it. */
export interface ArchivedSignal {
  readonly signal_id: string
  readonly symbol: string
  readonly timeframe: string
  readonly direction: string
  readonly archetype: string
  readonly grade: string
  readonly confidence: string
  readonly published_at: string
  readonly versions: Versions
  /** Absent while the signal is live — never null-fielded (§18.8). */
  readonly outcome?: {
    readonly outcome: string
    readonly resolved_at: string | null
    readonly elapsed_candles: number | null
    readonly mfe_r: string | null
    readonly mae_r: string | null
    /** PRD FC-10.1: in the archive, out of the statistics. */
    readonly excluded_from_stats: boolean
  }
}

/** One §18.8 statistics group — always version-segmented. */
export interface StatsGroup {
  readonly group_by: string
  readonly key: string | null
  readonly algo_version: string
  readonly counts: {
    readonly resolved: number
    readonly success: number
    readonly failed: number
    readonly expired: number
    readonly invalidated_early: number
  }
  readonly hit_rate: {
    readonly rated: number
    /** Null, never zero, when nothing was rated. */
    readonly rate_pct: string | null
    readonly confidence_interval: {
      readonly level: string
      readonly low_pct: string
      readonly high_pct: string
    } | null
    readonly sufficient_for_inference: boolean
    /** PRD FC-10.1's own phrasing, carried in the payload. */
    readonly label: string
  }
}

/** §18.3's hub, restricted to what is measurable. */
export interface DashboardOverview {
  readonly top_signals: readonly {
    readonly rank: number
    readonly signal_id: string
    readonly symbol: string
    readonly timeframe: string
    readonly direction: string
    readonly archetype: string
    readonly grade: string
    readonly confidence: string
    readonly display_rank: string
    readonly lifecycle_state: string
  }[]
  readonly live_total: number
  readonly recent_sweeps: readonly {
    readonly symbol: string
    readonly timeframe: string
    readonly pool_id: string
    /** Null when the pool row no longer exists — never guessed. */
    readonly side: string | null
    readonly event: string
    readonly reason: string
    readonly at: string
  }[]
  readonly not_measured: readonly string[]
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
    /**
     * §18.6's denominators. Present on the rankings row only, and optional
     * for the same reason `live_total` is: a screen must decide what an absent
     * count means rather than be handed a zero it cannot tell from a real one.
     */
    readonly gate_passers?: number
    readonly below_floor?: number
    /** §1.4's two sevens, published so a counter has a denominator. */
    readonly required_observation_days?: number
    readonly required_promotion_days?: number
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
