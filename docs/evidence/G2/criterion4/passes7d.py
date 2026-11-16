# Read-only. Exact 7-day window after shard T0: per-context work, overlap with the other engine, boundary lag.
import json, subprocess, statistics as st
from collections import defaultdict
from datetime import datetime, timedelta, timezone
TF = {"M5": 5, "M15": 15, "H1": 60, "H4": 240, "D1": 1440}
T0 = datetime.fromisoformat("2026-10-28T04:20:39+00:00"); END = T0 + timedelta(days=7)
def q(xs, p):
    xs = sorted(xs); return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]
def line(label, xs): print(f"{label:34s} n={len(xs):6d} p50={q(xs,50):6.3f} p95={q(xs,95):6.3f} p99={q(xs,99):6.3f} max={max(xs):6.3f} >2s={sum(x>2 for x in xs)}")
P = []
for name, eng in (("scanner-dev-engine-1", "E0"), ("scanner-dev-engine-shard1-1", "E1")):
    o = subprocess.run(["docker", "logs", "--since", T0.isoformat(), "--until", END.isoformat(), name], capture_output=True, text=True)
    for l in (o.stdout + o.stderr).splitlines():
        if "detection_pass_completed" not in l: continue
        try: r = json.loads(l[l.index("{"):])
        except Exception: continue
        done = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")); e = float(r["elapsed_seconds"])
        close = datetime.fromisoformat(r["open_time"]) + timedelta(minutes=TF[r["timeframe"]])
        P.append(dict(eng=eng, sym=r["symbol"], tf=r["timeframe"], e=e, done=done.timestamp(), start=done.timestamp()-e, close=close, pools=r.get("pools_upserted"), ev=r.get("events_inserted")))
print("window", T0.isoformat(), "->", END.isoformat())
print("passes by TF:", {t: sum(p["tf"]==t for p in P) for t in TF})
print("duplicates (same sym,tf,close):", len(P) - len({(p['sym'],p['tf'],p['close']) for p in P}))
line("ALL per-context", [p["e"] for p in P])
for g in ("E0","E1"): line(g, [p["e"] for p in P if p["eng"]==g])
print("symbols:", {g: sorted({p['sym'] for p in P if p['eng']==g}) for g in ("E0","E1")})
for t in TF: line("TF "+t, [p["e"] for p in P if p["tf"]==t])
print("\n--- per symbol, M5 only")
for s in sorted({p["sym"] for p in P}):
    xs=[p["e"] for p in P if p["sym"]==s and p["tf"]=="M5"]; g=next(p["eng"] for p in P if p["sym"]==s)
    print(f"{g} {s:10s} n={len(xs)} p50={q(xs,50):.3f} p95={q(xs,95):.3f}  pools p50={q([p['pools'] or 0 for p in P if p['sym']==s and p['tf']=='M5'],50)}")
# overlap: share of a pass's wall time during which the OTHER engine had a pass running
iv = {g: sorted((p["start"],p["start"]+p["e"]) for p in P if p["eng"]==g) for g in ("E0","E1")}
import bisect
starts={g:[a for a,_ in iv[g]] for g in iv}
def ov(p):
    o="E1" if p["eng"]=="E0" else "E0"; a,b=p["start"],p["start"]+p["e"]; i=max(bisect.bisect_left(starts[o],a)-1,0); s=0
    while i<len(iv[o]) and iv[o][i][0]<b:
        s+=max(0,min(b,iv[o][i][1])-max(a,iv[o][i][0])); i+=1
    return s/p["e"] if p["e"]>0 else 0
for p in P: p["ov"]=ov(p)
print("\n--- pass time by overlap with the other engine (same engine, M5 only, to hold the workload fixed)")
for g in ("E0","E1"):
    for name,lo,hi in (("alone (ov<10%)",0,.1),("partly",.1,.9),("full (ov>=90%)",.9,1.01)):
        xs=[p["e"] for p in P if p["eng"]==g and p["tf"]=="M5" and lo<=p["ov"]<hi]
        if xs: line(f"{g} M5 {name}", xs)
print("\n--- same, one symbol each, to hold the symbol fixed")
for g,s in (("E0","BTCUSDT"),("E1","DOGEUSDT"),("E1","ZECUSDT")):
    for name,lo,hi in (("alone",0,.1),("full",.9,1.01)):
        xs=[p["e"] for p in P if p["sym"]==s and p["tf"]=="M5" and lo<=p["ov"]<hi]
        if xs: line(f"{s} M5 {name}", xs)
print("\nshare of passes that were alone: ", {g: round(sum(p['ov']<.1 for p in P if p['eng']==g)/sum(p['eng']==g for p in P),3) for g in ("E0","E1")})
# boundary lag, exact window
B=defaultdict(list)
for p in P: B[p["close"]].append(p["done"])
def due(c):
    m=int(c.timestamp()//60); return [t for t,x in TF.items() if m%x==0]
rows=defaultdict(list); inc=0
for c,ds in B.items():
    if len(ds)!=15*len(due(c)): inc+=1; continue
    lag=max(ds)-c.timestamp(); rows[len(ds)].append(lag); rows["all"].append(lag)
print("\nboundaries complete",len(rows["all"]),"incomplete",inc,"(incomplete = cut by the window edges)")
for k in (15,30,45,60,75,"all"): line(f"boundary {k}", rows[k])
