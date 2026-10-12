"""SLS §14 row 1 as measured today: per close boundary, close -> the LAST pass of that boundary done. Read-only.

Run on the VM:   python3 - < ops/soak/boundary_last_pass.py      (nothing is written on the VM)

Input: `docker logs --since T0 scanner-dev-engine-1`, every `detection_pass_completed` line.
A boundary is one close instant (e.g. 12:15:00). Its passes are every (symbol, timeframe)
whose candle closes at that instant. "last done" is the newest pass timestamp among them.
A boundary counts as complete only when it has exactly symbols x due-timeframes passes;
incomplete ones (T0's first minutes, a restart, a missing log line) are reported, not used.
"""
import json
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone

TF = {"M5": 5, "M15": 15, "H1": 60, "H4": 240, "D1": 1440}
T0 = open("/home/ubuntu/soak-logs/T0").read().strip()
out = subprocess.run(["docker", "logs", "--since", T0, "scanner-dev-engine-1"], capture_output=True, text=True)

passes = defaultdict(list)          # close -> [(symbol, tf, done)]
symbols = set()
for line in (out.stdout + out.stderr).splitlines():
    if "detection_pass_completed" not in line:
        continue
    try:
        r = json.loads(line[line.index("{"):])
    except Exception:
        continue
    close = datetime.fromisoformat(r["open_time"]) + timedelta(minutes=TF[r["timeframe"]])
    done = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
    passes[close].append((r["symbol"], r["timeframe"], done))
    symbols.add(r["symbol"])


def due(close):
    minutes = int(close.timestamp() // 60)
    return [tf for tf, m in TF.items() if minutes % m == 0]


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


n_sym = len(symbols)
complete, incomplete = defaultdict(list), []
for close, ps in sorted(passes.items()):
    expected = n_sym * len(due(close))
    distinct = {(s, tf) for s, tf, _ in ps}
    if len(distinct) != expected:
        incomplete.append((close, len(distinct), expected))
        continue
    last = max(d for *_, d in ps)
    complete[expected].append((last - close).total_seconds())

print(f"soak T0 {T0}; measured at {datetime.now(timezone.utc).isoformat(timespec='seconds')}; "
      f"symbols seen {n_sym}; boundaries {len(passes)} (complete {sum(map(len, complete.values()))}, incomplete {len(incomplete)})")
print(f"{'passes/boundary':>16s} {'due TFs':>18s} {'n':>6s} {'p50':>8s} {'p95':>8s} {'p99':>8s} {'max':>8s}   close -> last pass done (s)")
names = {n_sym * k: "+".join(list(TF)[:k]) for k in range(1, 6)}
everything = []
for size in sorted(complete):
    xs = complete[size]
    everything += xs
    print(f"{size:16d} {names.get(size, '?'):>18s} {len(xs):6d} {q(xs,50):8.2f} {q(xs,95):8.2f} {q(xs,99):8.2f} {max(xs):8.2f}")
print(f"{'all':>16s} {'':>18s} {len(everything):6d} {q(everything,50):8.2f} {q(everything,95):8.2f} {q(everything,99):8.2f} {max(everything):8.2f}")
print("SLS §14 row 1 target: <= 30 s p95, <= 60 s p99")
if incomplete:
    print("incomplete boundaries (close, passes seen, expected), first 10:")
    for c, seen, exp in incomplete[:10]:
        print(f"  {c.isoformat()}  {seen}/{exp}")
