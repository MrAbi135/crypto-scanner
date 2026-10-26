"""G2 criterion 5 audit data: every signal in a window, with its evidence resolved. Read-only.

Run on the VM:   python3 - START END < audit_data.py > audit.json
                 (START/END are ISO UTC, e.g. 2026-10-10T14:04:42Z 2026-10-17T14:04:42Z;
                  with no arguments: ~/soak-logs/T0 to T0 + 7 days)

Every query runs with default_transaction_read_only = on. Nothing is written
anywhere except this script's stdout.

Evidence: `setups.evidence.attribution` names, per factor, the code that earned
points and the id of the record behind it. Each id is looked up in every table
that can hold it (engine events, zones, pools, and their transitions /
interactions). An id found nowhere is reported as such -- never dropped.
"""
import json
import subprocess
import sys
from datetime import datetime, timedelta

PSQL = ["docker", "exec", "-i", "scanner-dev-db-1", "psql", "-U", "scanner", "-d", "scanner",
        "-qAt", "-v", "ON_ERROR_STOP=1"]


def q(sql, **params):
    args = list(PSQL)
    for k, v in params.items():
        args += ["-v", f"{k}={v}"]
    out = subprocess.run(args, input="SET default_transaction_read_only = on;\n" + sql,
                         capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit("query failed: " + out.stderr.strip())
    return [json.loads(line) for line in out.stdout.splitlines() if line.strip()]


if len(sys.argv) >= 3:
    start, end = sys.argv[1], sys.argv[2]
else:
    t0 = open("/home/ubuntu/soak-logs/T0").read().strip()
    t0d = datetime.fromisoformat(t0[:19])
    start, end = t0[:19] + "Z", (t0d + timedelta(days=7)).isoformat() + "Z"

signals = q("""
select json_build_object(
  'signal_id', g.signal_id, 'setup_id', g.setup_id, 'symbol', g.symbol, 'timeframe', g.timeframe,
  'direction', g.direction, 'archetype', g.archetype, 'grade', g.grade,
  'published_at', to_char(g.published_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
  'algo_version', g.algo_version, 'payload', g.payload::json,
  'setup_evidence', s.evidence::json,
  'outcome', o.outcome,
  'resolved_at', to_char(o.resolved_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
  'realised_r', o.realised_r, 'mfe_r', o.mfe_r, 'mae_r', o.mae_r)
from detection.signals g
left join detection.setups s on s.setup_id = g.setup_id
left join detection.signal_outcomes o on o.signal_id = g.signal_id
where g.published_at >= :'start'::timestamptz and g.published_at < :'end'::timestamptz
order by g.published_at;
""", start=start, end=end)

ids = set()
for sg in signals:
    ev = sg.get("setup_evidence") or {}
    for items in (ev.get("attribution") or {}).values():
        for it in items:
            if it.get("evidence_id"):
                ids.add(it["evidence_id"])
    for i in (sg["payload"] or {}).get("evidence_ids") or []:
        ids.add(i)
symbols = sorted({sg["symbol"] for sg in signals})

records = {}
if ids:
    id_list = ",".join(sorted(ids))
    sym_list = ",".join(symbols)
    rows = q("""
with i as (select unnest(string_to_array(:'ids', ',')) as id)
select json_build_object('id', e.event_key, 'table', 'engine_events', 'type', e.event_type,
  'timeframe', e.timeframe, 'at', to_char(e.event_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI"Z"'),
  'detail', e.payload::json)
  from detection.engine_events e join i on e.event_key = i.id
  where e.symbol = any(string_to_array(:'syms', ','))
union all
select json_build_object('id', z.zone_id, 'table', 'ict_zones', 'type', z.zone_type || ' ' || z.polarity,
  'timeframe', z.timeframe, 'at', to_char(z.created_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI"Z"'),
  'detail', json_build_object('band_low', z.band_low, 'band_high', z.band_high, 'grade', z.grade,
                              'state', z.state))
  from detection.ict_zones z join i on z.zone_id = i.id
union all
select json_build_object('id', p.pool_id, 'table', 'liquidity_pools',
  'type', p.side || ' ' || p.liquidity_class || ' (' || p.source || ')',
  'timeframe', p.timeframe, 'at', to_char(p.created_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI"Z"'),
  'detail', json_build_object('price', p.price, 'band_low', p.band_low, 'band_high', p.band_high,
                              'members', p.member_count, 'state', p.state))
  from detection.liquidity_pools p join i on p.pool_id = i.id
union all
select json_build_object('id', t.transition_id, 'table', 'liquidity_transitions',
  'type', 'pool ' || t.from_state || ' -> ' || t.to_state,
  'timeframe', t.timeframe, 'at', to_char(t.transitioned_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI"Z"'),
  'detail', json_build_object('reason', t.reason))
  from detection.liquidity_transitions t join i on t.transition_id = i.id
union all
select json_build_object('id', t.transition_id, 'table', 'ict_zone_transitions',
  'type', 'zone ' || t.from_state || ' -> ' || t.to_state,
  'timeframe', t.timeframe, 'at', to_char(t.transitioned_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI"Z"'),
  'detail', json_build_object('reason', t.reason))
  from detection.ict_zone_transitions t join i on t.transition_id = i.id
union all
select json_build_object('id', x.interaction_id, 'table', 'ict_zone_interactions',
  'type', 'zone ' || x.kind, 'timeframe', x.timeframe,
  'at', to_char(x.observed_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI"Z"'),
  'detail', json_build_object('close', x.close_price, 'depth', x.penetration_depth))
  from detection.ict_zone_interactions x join i on x.interaction_id = i.id;
""", ids=id_list, syms=sym_list)
    for r in rows:
        records.setdefault(r["id"], []).append(r)

json.dump({"window": {"start": start, "end": end}, "signals": signals, "records": records,
           "ids_total": len(ids), "ids_found": len(records)}, sys.stdout, default=str)
