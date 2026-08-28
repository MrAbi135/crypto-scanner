-- Correctness invariants for the G1b soak.
--
-- `soak_status.sh` answers "is the engine alive?" -- passes happening, none
-- stale, none failing. It was green on 2026-08-26 while the BOS gate had been
-- latched in one direction for five days, no up-impulse leg could be built on
-- any symbol, ninety per cent of the event table was orphaned debris, and F3
-- scored zero on every setup ever recorded.
--
-- None of that is a liveness question, so nothing asked it. Every one of those
-- defects was found by hand, by querying production, days after it started.
-- This file is those queries, so they are asked every hour instead.
--
-- **Contract.** Each check emits one row per violation and nothing at all when
-- it is satisfied. `check_invariants.sh` counts the rows. A check that cannot
-- emit a row is worse than no check, so each one below states what would make
-- it fire and each carries a minimum sample -- an invariant asserted over four
-- setups is an opinion.

\pset border 2
\pset footer off
\timing off


-- **Check A lives in the runner, not here.** "Labels in direction X but no
-- break in X" fires on every healthy series: §3.5 only breaks *with* the
-- trend, so a bullish symbol prints lower lows and correctly records no
-- BOS_DOWN. The first draft of this file asserted it in SQL and flagged five
-- contexts, all five of them working exactly as the doctrine says. A check
-- that cries wolf hourly is not a weak check, it is a check that gets ignored.
--
-- Deciding it needs the maintained trend, which lives in Redis rather than in
-- any table, so `check_invariants.sh` asks Redis for the direction first and
-- only then asks Postgres whether that direction is still breaking.


-- ===========================================================================
-- B. An event written more than once for the same fact
-- ===========================================================================
-- 163,446 rows for 16,597 events on 2026-08-26, one of them repeated 1,980
-- times. Identity keyed on something that is not durable across the sliding
-- window rewrites the same fact every pass; see the window-local index class.
--
-- Liquidity events are keyed per pool as well, so their logical key includes
-- the pool. Everything else is one row per (symbol, tf, type, time).

with keyed as (
    select event_type,
           count(*) as rows,
           count(distinct (symbol, timeframe, event_at, algo_version,
                           case when event_type like 'LIQUIDITY%'
                                then payload::json ->> 'pool_id' end)) as logical
    from detection.engine_events
    group by 1
)
select 'B. duplicated events' as check,
       event_type, rows, logical,
       round(rows::numeric / nullif(logical, 0), 1) as factor
from keyed
where rows >= 20                            -- a 2/1 split on three rows is noise
  and rows::numeric / nullif(logical, 0) > 1.1;


-- ===========================================================================
-- C. A liquidity event pointing at a pool that does not exist
-- ===========================================================================
-- 147,875 such rows on 2026-08-26, left behind when pool identity was moved
-- off the window-local index. Harmless to read and not harmless to carry: they
-- were 79% of every row an H1 pass loaded.
--
-- **The pool reference is not called the same thing in every payload.** A
-- sweep writes `pool_id`; a stop hunt writes `sweep_pool_id`, because §4.7
-- builds it *on* a sweep and also carries a `displacement_id` that would be
-- ambiguous otherwise. The first version of this check read `pool_id` from
-- both, so on a stop hunt it joined on NULL, matched nothing, and reported
-- every single one as orphaned -- a check that could not pass, of exactly the
-- kind this file exists to find.
--
-- It was not only noise. The same predicate had been used to clean the table
-- an hour earlier, and it deleted 5,907 stop-hunt rows that were not orphaned
-- at all. Reading a missing field as a missing *pool* is the whole error, so
-- the third clause below refuses to guess: an event carrying neither key is
-- reported as an unreadable shape rather than silently counted as a violation
-- or silently skipped.
--
-- Recent only. History from before an identity change is debris to be cleaned
-- once, not a live defect to alarm on every hour.

select 'C. orphaned liquidity events' as check,
       e.symbol, e.timeframe, e.event_type, count(*) as rows
from detection.engine_events e
left join detection.liquidity_pools p
       on p.pool_id = coalesce(e.payload::json ->> 'pool_id',
                               e.payload::json ->> 'sweep_pool_id')
where e.event_type like 'LIQUIDITY%'
  and e.created_at > now() - interval '6 hours'
  -- Only rows that name a pool. One that names none is the next check's
  -- business, not evidence of an orphan.
  and coalesce(e.payload::json ->> 'pool_id',
               e.payload::json ->> 'sweep_pool_id') is not null
  and p.pool_id is null
group by 1, 2, 3, 4;

-- C2. A liquidity payload this check cannot read.
--
-- If a new event type arrives, or a payload is renamed again, the join above
-- would quietly have nothing to test and report clean. This makes that
-- condition loud instead: the check saying "I do not know how to check this"
-- is worth more than the check saying nothing.

select 'C2. liquidity payload names no pool' as check,
       e.event_type, count(*) as rows,
       (array_agg(distinct left(e.payload, 120)))[1] as sample
from detection.engine_events e
where e.event_type like 'LIQUIDITY%'
  and e.created_at > now() - interval '6 hours'
  and e.payload::json ->> 'pool_id' is null
  and e.payload::json ->> 'sweep_pool_id' is null
group by 1, 2;


-- ===========================================================================
-- D. A confluence factor that has never scored
-- ===========================================================================
-- F3 was zero on every setup the engine had recorded. Two separate reasons --
-- a zone family that could not reach the freshness ladder, and candidates
-- landing on bare OTE bands, which §8.3 does not grade -- and both were
-- invisible because a factor at zero looks like a weak setup, not a broken one.
--
-- §8.3 says each factor is 0-100. A factor whose *maximum* across a meaningful
-- sample is zero is not scoring low; it is not wired.

with scored as (
    select f.key as factor, max((f.value #>> '{}')::numeric) as best, count(*) as n
    from detection.setups s,
         lateral json_each(s.factor_scores::json) f
    group by 1
)
select 'D. factor never scores' as check, factor, n as setups_seen, best
from scored
where n >= 10 and best = 0;


-- ===========================================================================
-- E. An archetype term that is never satisfied
-- ===========================================================================
-- `displaced_bos` was unmet on 100% of setups for as long as setups existed,
-- and it was unmet for three different reasons in succession. A term that no
-- candidate has ever met is either broken or unreachable, and §8.6 does not
-- have unreachable terms.
--
-- Reported per archetype, because A1's terms and A3's fail for different
-- reasons and a pooled count would hide whichever is rarer.

--
-- **One term is exempt, with a reason and a count.** A2's `breaker_formed`
-- is unmet on every setup because breakers are genuinely rare, not because
-- anything is broken. Measured on the host 2026-08-27, the funnel narrows for
-- reasons §5.1 and §5.4 each state:
--
--     OB zones ever                        234
--       ... INVALIDATED                    162
--       ... AND origin_swept = true         18
--       ... AND external structure break      8
--     BREAKER zones ever                     4
--
-- Eight candidates, four breakers, one live. `breaker_formed` wants a BRK_A
-- among the zones at price and of the right polarity, so it will be unmet for
-- long stretches of any ordinary market. Left in, this check would fire every
-- hour forever, and a check that cries wolf hourly is not a weak check -- it
-- is one that gets ignored, taking the rest of the file with it.
--
-- The exemption is by (archetype, term) and not by term, so the same word
-- under a different archetype still fires; and a *new* never-met term fires
-- however rare anyone believes it to be. If breakers are ever wired
-- differently, delete the row rather than widening it.

with unmet as (
    select a.key as archetype, term.value #>> '{}' as term, count(*) as times
    from detection.setups s,
         lateral json_each(s.evidence::json -> 'archetype_unmet') a,
         lateral json_array_elements(a.value) term
    group by 1, 2
),
total as (select count(*) as n from detection.setups),
rare_by_doctrine(archetype, term) as (
    values ('A2', 'breaker_formed')
)
select 'E. archetype term never met' as check,
       u.archetype, u.term, u.times, t.n as setups_seen
from unmet u, total t
where t.n >= 10
  and u.times = t.n
  and not exists (
        select 1 from rare_by_doctrine r
         where r.archetype = u.archetype and r.term = u.term
      );


-- ===========================================================================
-- F. A zone state or grade the scorer cannot pay for
-- ===========================================================================
-- §8.3.1's ladder is written in a mixed vocabulary -- `FRESH` and `TESTED`
-- from `ZoneState`, `CE_FILLED` from `FvgState` -- so a family that speaks
-- neither word scores zero for a condition it is genuinely in. `MITIGATED`
-- was missing for exactly that reason.
--
-- OTE and BPR are *deliberately* ungraded and are listed here so that adding
-- a new zone type without deciding its award fires this check rather than
-- silently scoring it nothing. §8.3.1's table changes only by amendment
-- (Constitution §30.8), so a new grade is a question for the developer.

select 'F. zone grade cannot score' as check,
       'grade' as dimension, grade as value, count(*) as live_zones
from detection.ict_zones
where state not in ('INVALIDATED', 'EXPIRED', 'FILLED', 'INVERTED', 'DEAD')
  and grade not in ('BRK_A', 'OB_A', 'OB_B', 'FVG', 'MIT', 'IFVG')
  -- §8.3 grades locations; §5.8 makes OTE a stack overlay (its worked example
  -- is "OB_A in an OTE stack") and §5.6 defers BPR scoring to Future.
  and grade not in ('OTE', 'BPR')
group by 1, 2, 3;

select 'F. zone state cannot score' as check,
       'state' as dimension, state as value, count(*) as live_zones
from detection.ict_zones
where state not in ('INVALIDATED', 'EXPIRED', 'FILLED', 'INVERTED', 'DEAD')
  -- Paid directly, or translated by `_FVG_STATE_EQUIVALENT`.
  and state not in ('FRESH', 'TESTED', 'CE_FILLED', 'OPEN', 'TOUCHED', 'MITIGATED')
  -- §5.5 gives an unproven IFVG no rung, which is a decision rather than a gap.
  and state <> 'UNPROVEN'
group by 1, 2, 3;


-- ===========================================================================
-- G. Detection falling behind its own candles
-- ===========================================================================
-- Per symbol and timeframe rather than globally: `soak_status.sh` reads the
-- newest pass in the log, so one context can stop being detected while the
-- others keep the aggregate looking fresh.

with tf(name, step) as (
    values ('M5', interval '5 min'), ('M15', interval '15 min'),
           ('H1', interval '1 hour'), ('H4', interval '4 hours')
),
newest as (
    select c.symbol, c.timeframe,
           max(c.open_time) as last_candle,
           (select max(e.event_at) from detection.engine_events e
             where e.symbol = c.symbol and e.timeframe = c.timeframe) as last_event
    from market.candles c
    group by 1, 2
)
select 'G. detection behind candles' as check,
       n.symbol, n.timeframe, n.last_candle,
       coalesce(n.last_event::text, '(never)') as last_event
from newest n
join tf on tf.name = n.timeframe
where n.last_event is null
   or n.last_candle - n.last_event > tf.step * 12;


-- ===========================================================================
-- H. The daily universe loop, still running
-- ===========================================================================
-- §1.4 promotes a symbol on seven consecutive daily evaluations, and the
-- snapshot refuses to build under seven daily observations -- two sevens in
-- series. Until the first completes, every symbol sits at
-- `INELIGIBLE / QUARANTINE` with `consecutive_passes = 0`.
--
-- **That is indistinguishable from the loop being dead**, which is the whole
-- reason for this check. If the worker's midnight job stopped, the table would
-- look exactly as it does today and nothing would notice: no error, no
-- backlog, no stale pass -- just a promotion that never arrives, weeks later.
--
-- Two days of grace. The job runs at UTC midnight, so one missed day could be
-- a restart landing across it; two consecutive is the loop.

with newest as (
    select max(observed_at)::date as last_day from market.liquidity_history
)
select 'H. daily universe loop has stopped' as check,
       coalesce(last_day::text, '(no observations at all)') as last_observation,
       (current_date - coalesce(last_day, date '2000-01-01')) as days_ago
from newest
where last_day is null or current_date - last_day > 2;

-- H2. Accumulating, but not for everything it syncs.
--
-- The loop iterates `list_observable()`; a symbol that is observable and has
-- no history is one the collector is failing on, and it would wait for its
-- seven days forever while its neighbours advance.

select 'H2. observable symbol with no history' as check,
       s.exchange_symbol, s.status
from market.symbols s
left join market.liquidity_history h on h.exchange_symbol = s.exchange_symbol
where s.status = 'QUARANTINE'
  and h.exchange_symbol is null
  -- Only once the loop has had a night to reach them.
  and exists (select 1 from market.liquidity_history)
limit 10;
