"""Close-to-detection latency breakdown since the current soak T0. Read-only.

Run on the VM:   python3 - < ops/soak/latency_baseline.py      (nothing is written on the VM)
Re-run the same file after a deploy and compare the two outputs line by line.

Stages, per detection pass:
  close  -> stored    candle closes -> ingest has written it (Binance delivery + our parse/insert)
  stored -> stream    our outbox relay (polls every _RELAY_INTERVAL_SECONDS)
  stream -> start     waiting in the engine's queue
  pass                the pass itself (engine's own elapsed_seconds)
  total               close -> pass done
Inputs: ~/soak-logs/T0, `docker logs` of engine and ingest since T0, and the newest
6000 entries of scanner:stream:candle-closed (XREVRANGE, read-only).
"""
import json
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone

TF = {"M5": 5, "M15": 15, "H1": 60, "H4": 240, "D1": 1440}
T0 = open("/home/ubuntu/soak-logs/T0").read().strip()


def sh(*cmd):
    out = subprocess.run(list(cmd), capture_output=True, text=True)
    return out.stdout + out.stderr


raw = sh("docker", "exec", "scanner-dev-redis-1", "redis-cli", "--raw", "XREVRANGE",
         "scanner:stream:candle-closed", "+", "-", "COUNT", "6000").splitlines()
pub = {}
i = 0
while i < len(raw):
    eid = raw[i]
    if "-" in eid and eid.split("-")[0].isdigit():
        j = i + 1
        f = {}
        while j + 1 < len(raw) and not ("-" in raw[j] and raw[j].split("-")[0].isdigit() and len(raw[j]) > 12):
            f[raw[j]] = raw[j + 1]
            j += 2
        try:
            p = json.loads(f.get("payload", "{}")).get("payload", {})
            pub[(p["symbol"], p["timeframe"], p["open_time"])] = int(eid.split("-")[0]) / 1000
        except Exception:
            pass
        i = j
    else:
        i += 1

stored = {}
for line in sh("docker", "logs", "--since", T0, "scanner-dev-ingest-1").splitlines():
    if '"live_candle_ingested"' in line:
        r = json.loads(line[line.index("{"):])
        stored[(r["symbol"], r["timeframe"], r["open_time"])] = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))

# Every engine process: speed step 1 runs two shards, each passing its own
# symbols. A container that does not exist contributes nothing.
ENGINES = ("scanner-dev-engine-1", "scanner-dev-engine-shard1-1")
engine_text = "".join(sh("docker", "logs", "--since", T0, name) for name in ENGINES)

rows = []
for line in engine_text.splitlines():
    if "detection_pass_completed" not in line:
        continue
    try:
        r = json.loads(line[line.index("{"):])
    except Exception:
        continue
    key = (r["symbol"], r["timeframe"], r["open_time"])
    if key not in pub or key not in stored:
        continue
    close = datetime.fromisoformat(r["open_time"]) + timedelta(minutes=TF[r["timeframe"]])
    done = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
    start = done - timedelta(seconds=r["elapsed_seconds"])
    streamed = datetime.fromtimestamp(pub[key], tz=timezone.utc)
    rows.append({
        "tf": r["timeframe"], "close": close,
        "close->stored": (stored[key] - close).total_seconds(),
        "stored->stream": (streamed - stored[key]).total_seconds(),
        "stream->start": (start - streamed).total_seconds(),
        "pass": r["elapsed_seconds"],
        "total": (done - close).total_seconds(),
    })


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


print(f"soak T0 {T0}; measured at {datetime.now(timezone.utc).isoformat(timespec='seconds')}; passes matched {len(rows)}")
stages = ("close->stored", "stored->stream", "stream->start", "pass", "total")
print(f"{'stage':16s} {'p50':>8s} {'p95':>8s} {'p99':>8s} {'max':>8s}")
for s in stages:
    xs = [r[s] for r in rows]
    print(f"{s:16s} {q(xs,50):8.3f} {q(xs,95):8.3f} {q(xs,99):8.3f} {max(xs):8.3f}")
print("\ntotal by timeframe:")
for tf in TF:
    xs = [r["total"] for r in rows if r["tf"] == tf]
    if xs:
        print(f"  {tf:4s} n={len(xs):5d} p50={q(xs,50):7.2f}s p95={q(xs,95):7.2f}s")
size = defaultdict(int)
for r in rows:
    size[r["close"]] += 1
print("\ntotal by passes per boundary:")
for n in sorted(set(size.values())):
    xs = [r["total"] for r in rows if size[r["close"]] == n]
    print(f"  {n:3d} passes/boundary  n={len(xs):5d} p50={q(xs,50):7.2f}s p95={q(xs,95):7.2f}s")
