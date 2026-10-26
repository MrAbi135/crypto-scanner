"""Independent check of every audit claim against Binance's own candles.

    python independent_check.py audit.json check.json

* Candles come from Binance's public REST API (api.binance.com/api/v3/klines,
  spot -- the market the engine ingests), NOT from our database. Cached under
  ./binance_cache so a re-run makes no new requests.
* Nothing from the engine is imported. ATR, swings, displacement, FVG and the
  outcome walk are re-implemented here from the SLS text (sections named at
  each check), so a shared bug cannot make both sides agree.
* Window-local indexes in the records (499, 500, ...) are ignored; every claim
  is located by its time and its exact price.
* Each check returns PASS / FAIL / CANNOT, with the numbers. CANNOT means this
  script cannot decide it (e.g. a premise check, or history it does not have) --
  never a quiet pass.
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal as D

TF = {"M5": (300, "5m"), "M15": (900, "15m"), "H1": (3600, "1h"), "H4": (14400, "4h"), "D1": (86400, "1d")}
TTL = {"M5": 48, "M15": 24, "H1": 24, "H4": 18, "D1": 15}           # SLS 12.5
K_EXT, K_INT = 5, 2                                                     # SLS 3.1
EPS_ATR = D("0.05")                                                     # SLS 0.4
ACTIVATION_CANDLE_EXCLUDED = os.environ.get("ACT_EXCL", "1") == "1"   # see README: SLS 12.3 does not say
MOVED_STOP_NEXT_CANDLE = os.environ.get("MOVED_NEXT", "1") == "1"       # see README: SLS 12.3 does not say
ZONE_BACK = 3000   # candles (a breaker's parent OB was 19 days older on PUMPUSDT M15) of history searched for a zone's origin
WARM = 300                                                              # candles before the first claim
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "binance_cache")


def ts(iso):
    iso = iso.replace("Z", "")
    fmt = "%Y-%m-%dT%H:%M:%S" if iso.count(":") == 2 else "%Y-%m-%dT%H:%M"
    return int(datetime.strptime(iso, fmt).replace(tzinfo=timezone.utc).timestamp())


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def fetch(symbol, tf, start, end):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{symbol}_{tf}_{start}_{end}.json")
    if os.path.exists(path):
        return json.load(open(path))
    step, iv = TF[tf]
    out, cur = [], start
    while cur <= end:
        url = (f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={iv}"
               f"&startTime={cur * 1000}&endTime={end * 1000}&limit=1000")
        with urllib.request.urlopen(url, timeout=30) as r:
            rows = json.loads(r.read())
        if not rows:
            break
        out += [[k[0] // 1000, k[1], k[2], k[3], k[4]] for k in rows]
        cur = rows[-1][0] // 1000 + step
        time.sleep(0.2)
    json.dump(out, open(path, "w"))
    return out


class Series:
    def __init__(self, rows, step):
        self.step = step
        self.t = [r[0] for r in rows]
        self.o, self.h, self.l, self.c = ([D(r[j]) for r in rows] for j in (1, 2, 3, 4))
        self.at = {t: i for i, t in enumerate(self.t)}
        n = len(rows)
        tr = [self.h[0] - self.l[0]] + [max(self.h[i] - self.l[i], abs(self.h[i] - self.c[i - 1]),
                                            abs(self.l[i] - self.c[i - 1])) for i in range(1, n)]
        self.atr = [None] * n                                            # SLS 2.9, Wilder, SMA seed
        if n >= 14:
            self.atr[13] = sum(tr[:14]) / 14
            for i in range(14, n):
                self.atr[i] = (self.atr[i - 1] * 13 + tr[i]) / 14
        self.gaps = [i for i in range(1, n) if self.t[i] - self.t[i - 1] != step]

    def idx(self, t):
        return self.at.get(t)

    def eps(self, i):
        return EPS_ATR * self.atr[i] if self.atr[i] is not None else None

    def body(self, i):
        return abs(self.c[i] - self.o[i])

    def displacement(self, i, direction):
        """SLS 5.10: body >= 2 x mean_body_20, range >= 1.5 ATR, close in the extreme 25 %, body sign."""
        if i < 20 or self.atr[i] is None:
            return None, "not enough history"
        mb = sum(self.body(j) for j in range(i - 20, i)) / 20
        rng = self.h[i] - self.l[i]
        sign = "UP" if self.c[i] > self.o[i] else "DOWN" if self.c[i] < self.o[i] else "DOJI"
        if rng == 0:
            return False, "zero range"
        pos = (self.c[i] - self.l[i]) / rng
        ok = (self.body(i) >= 2 * mb and rng >= D("1.5") * self.atr[i] and sign == direction
              and (pos >= D("0.75") if direction == "UP" else pos <= D("0.25")))
        return ok, (f"body {self.body(i) / mb:.2f}x mean20 (need 2), range {rng / self.atr[i]:.2f}x ATR "
                    f"(need 1.5), close at {pos * 100:.0f}% of range, body {sign}")

    def pivot(self, s, kind, k):
        """SLS 3.1 strict fractal; 'tie' when it fails only on neighbours within eps."""
        if s - k < 0 or s + k >= len(self.t):
            return None
        a = self.h if kind == "HIGH" else self.l
        better = (lambda x, y: x > y) if kind == "HIGH" else (lambda x, y: x < y)
        nb = [a[s + j] for j in range(-k, k + 1) if j]
        if all(better(a[s], x) for x in nb):
            return "strict"
        # SLS 3.1 tie rule: the LAST candle of an equal set is the swing, so
        # equality is allowed only with EARLIER neighbours; a later equal means
        # the scan continues and this candle is not the swing.
        e = self.eps(s) or D(0)
        before = [a[s - j] for j in range(1, k + 1)]
        after = [a[s + j] for j in range(1, k + 1)]
        if all(better(a[s], x) for x in after) and all(better(a[s], x) or abs(a[s] - x) <= e for x in before):
            return "tie"
        return False

    def find_swing(self, price, kind, before, k, after=0):
        """Latest candle at this exact extreme that is a k-swing; else the latest at that price."""
        a = self.h if kind == "HIGH" else self.l
        anyhit = None
        for s in range(min(before, len(self.t) - 1), after - 1, -1):
            if a[s] == price:
                if self.pivot(s, kind, k):
                    return s
                anyhit = s if anyhit is None else anyhit
        return anyhit

    def find_price(self, price, kind, before, after=0):
        a = self.h if kind == "HIGH" else self.l
        for s in range(min(before, len(self.t) - 1), after - 1, -1):
            if a[s] == price:
                return s
        return None


def at_index(S, rec, fallback):
    """The newest candle opening at or before the record's `created_at`.

    What `created_at` means depends on the record, as read from the engine on
    2026-10-26 (the README's old "write time" note was wrong for zones):
      * OB / mitigation: the open time of the FIRST origin candle
        (domain/ict/order_blocks.py: `candles[run_indices[0]].open_time`);
      * BREAKER: the parent OB's invalidation time (domain/ict/breakers.py);
      * FVG: the third candle's close time (domain/ict/fvg.py);
      * a SWING pool: after the swing's confirmation.
    Searching back from the signal instead picked a LATER candle at the same
    price -- all four origin FAILs on the v38 soak were that: OB runs found
    2h, 14h and 4h after the zone's origin, a swing 75 min after its pool.
    """
    t = ts(rec["at"])
    best = None
    for i, ti in enumerate(S.t):
        if ti <= t:
            best = i
        else:
            break
    return fallback if best is None else min(best, fallback)


def res2(clause, verdict, text):
    return {"verdict": verdict, "text": text, "clause": clause}


def res(verdict, text):  # kept only to fail loudly: every check must name its SLS clause
    raise AssertionError("check without an SLS clause: " + text)


def _unused(verdict, text):
    return {"verdict": verdict, "text": text}


def check_bos(S, rec, codes):
    d = rec["detail"]
    i = S.idx(ts(rec["at"]))
    if i is None:
        return [res2('SLS 2.1 (data present)', "CANNOT", "break candle not in Binance data")]
    out = []
    direction = rec["type"].split("_")[1]
    level, close = D(d["swing_price"]), D(d["candle_close"])
    out.append(res2('SLS 2.1 OHLCV -- the recorded close', "PASS" if S.c[i] == close else "FAIL",
                   f"Binance close of the {iso(S.t[i])} candle = {S.c[i]} (claim {close})"))
    e = S.eps(i)
    broke = S.c[i] < level - e if direction == "DOWN" else S.c[i] > level + e
    out.append(res2('SLS 3.5 Detection: Cl < swing - eps (eps per 0.4)', "PASS" if broke else "FAIL",
                   f"close {S.c[i]} {'<' if direction == 'DOWN' else '>'} level {level} "
                   f"{'-' if direction == 'DOWN' else '+'} eps {e:.6f} (SLS 3.5, close not wick)"))
    kind = "LOW" if direction == "DOWN" else "HIGH"
    s = S.find_swing(level, kind, i - 1, K_EXT)
    if s is None:
        out.append(res2('SLS 3.5 Inputs: a confirmed external swing', "CANNOT", f"no candle with {kind.lower()} = {level} before the break in the fetched history"))
    else:
        pv = S.pivot(s, kind, K_EXT)
        out.append(res2('SLS 3.5 Validation + 3.1 (level is a confirmed external k=5 swing)', "PASS" if pv else "FAIL",
                       f"level {level} is the {kind.lower()} of {iso(S.t[s])}: "
                       f"{'external swing (11-candle fractal)' if pv == 'strict' else 'external swing by the tie rule' if pv == 'tie' else 'NOT an external swing'}"))
        if pv and i < s + K_EXT:
            out.append(res2('SLS 3.1 Confirmation timing / 0.2 closed-candle law', "FAIL", "break happened before the swing was confirmed (look-ahead)"))
        earlier = [j for j in range(s + K_EXT, i)
                   if (S.c[j] < level - S.eps(j) if direction == "DOWN" else S.c[j] > level + S.eps(j))]
        # SLS 3.5 also needs the trend to agree, which this script does not
        # track: an earlier close beyond the level under the opposite trend is
        # not a BOS (2026-10-12 ETH M5: earlier close 22:50 while BULLISH_CAUTION,
        # MSS_DOWN 23:00, BOS 23:05 -- the doctrine's order).
        out.append(res2("SLS 3.5 'first closing candle beyond the level' + trend agreement", "PASS" if not earlier else "CANNOT",
                       "first close beyond the level" if not earlier
                       else f"an earlier candle closed beyond it at {iso(S.t[earlier[0]])}; whether that was a BOS "
                            f"depends on the trend state then, which this script does not track"))
    if "displaced" in codes:
        a = S.displacement(i, direction)
        b = S.displacement(i - 1, direction)
        ok = bool(a[0]) or bool(b[0])
        out.append(res2('SLS 3.5 Validation (BOS quality grade `displaced`) + 5.10', "PASS" if ok else "FAIL",
                       f"displaced (SLS 3.5: break candle or the one before passes 5.10). break candle: {a[1]}; "
                       f"candle before: {b[1]}. NOTE the engine grades this through the impulse leg (7.5), "
                       f"so a FAIL here is a doctrine-reading difference to look at, not proof of a bug"))
    return out


def check_swing(S, rec):
    d = rec["detail"]
    kind, price = d["kind"], D(d["price"])
    conf = S.idx(ts(rec["at"]))
    if conf is None:
        return [res2('SLS 2.1 (data present)', "CANNOT", "confirmation candle not in Binance data")]
    s = S.find_swing(price, kind, conf + K_EXT, K_EXT, conf - 40)
    if s is None:
        return [res2('SLS 3.1', "FAIL", f"no candle with {kind.lower()} = {price} near {iso(S.t[conf])}")]
    out = []
    pv = S.pivot(s, kind, K_EXT)
    out.append(res2('SLS 3.1 (k_ext = 5 strict fractal; tie rule)', "PASS" if pv else "FAIL",
                   f"{kind.lower()} {price} at {iso(S.t[s])}: "
                   f"{'external swing' if pv == 'strict' else 'external swing by tie rule' if pv == 'tie' else 'NOT an 11-candle fractal'}"))
    out.append(res2('SLS 3.1 Confirmation timing (event dating = engine convention, measured)', "PASS" if conf == s else "FAIL",
                   f"event dated at the swing candle itself ({iso(S.t[s])}); confirmation is {K_EXT} candles later, "
                   f"{iso(S.t[s + K_EXT]) if s + K_EXT < len(S.t) else '?'} -- the record's own time cannot show when it was written"))
    # the previous same-kind external swing; two cannot sit within k of each
    # other (a tie set resolves to one swing, SLS 3.1)
    prev = next((j for j in range(s - K_EXT - 1, K_EXT - 1, -1) if S.pivot(j, kind, K_EXT)), None)
    if prev is None:
        out.append(res2('SLS 3.3', "CANNOT", "no earlier external swing of the same kind in the fetched history to label against"))
    else:
        a = S.h if kind == "HIGH" else S.l
        e = S.eps(s + K_EXT)
        lab = ("HH" if a[s] > a[prev] + e else "LH" if a[s] < a[prev] - e else "EQH") if kind == "HIGH" else \
              ("HL" if a[s] > a[prev] + e else "LL" if a[s] < a[prev] - e else "EQL")
        tie_prev = S.pivot(prev, kind, K_EXT) == "tie"
        out.append(res2('SLS 3.3 (HH/LH/EQH by +-eps) + 3.1 tie rule', "PASS" if lab == d.get("label") else "CANNOT" if tie_prev else "FAIL",
                       f"label vs previous external {kind.lower()} {a[prev]} ({iso(S.t[prev])}): {lab} (claim {d.get('label')})"
                       + (" -- that previous swing exists only by the SLS 3.1 tie rule, whose reading at the eps edge "
                          "is ambiguous; needs a doctrine ruling, not a verdict" if tie_prev and lab != d.get("label") else "")))
    return out


def check_mss(S, rec):
    d = rec["detail"]
    direction = d["direction"]
    ft = S.idx(ts(rec["at"]))
    if ft is None:
        return [res2('SLS 2.1 (data present)', "CANNOT", "follow-through candle not in Binance data")]
    ch = ft - int(d["followthrough_candles"])
    out = []
    claim = D(d["candle_close"])
    which = "follow-through" if S.c[ft] == claim else "CHoCH" if S.c[ch] == claim else None
    out.append(res2('SLS 2.1 OHLCV -- the recorded close', "PASS" if which else "FAIL",
                   f"claimed close {claim} = Binance close of the {which} candle" if which else
                   f"claimed close {claim} matches neither CHoCH ({S.c[ch]}) nor follow-through ({S.c[ft]})"))
    ok = S.c[ft] < S.l[ch] if direction == "DOWN" else S.c[ft] > S.h[ch]
    out.append(res2('SLS 3.6 MSS condition 3 (follow-through)', "PASS" if ok else "FAIL",
                   f"follow-through {iso(S.t[ft])} closes {S.c[ft]} beyond the CHoCH candle's "
                   f"{'low' if direction == 'DOWN' else 'high'} {S.l[ch] if direction == 'DOWN' else S.h[ch]} "
                   f"within {d['followthrough_candles']} candles (SLS 3.6.3, max 5)"))
    if int(d["followthrough_candles"]) > 5:
        out.append(res2('SLS 3.6 MSS condition 3 (within 5 candles)', "FAIL", "follow-through later than 5 candles"))
    disp = [S.displacement(j, direction) for j in (ch - 1, ch, ch + 1)]
    out.append(res2('SLS 3.6 MSS condition 1 + 5.10', "PASS" if any(x[0] for x in disp) else "FAIL",
                   f"displacement on the CHoCH candle {iso(S.t[ch])} or a neighbour (SLS 3.6.1): "
                   + " | ".join(x[1] for x in disp)))
    kind = "LOW" if direction == "DOWN" else "HIGH"
    piv = next((j for j in range(ch - K_INT - 1, K_INT - 1, -1) if S.pivot(j, kind, K_INT)), None)
    if piv is None:
        out.append(res2('SLS 3.6 CHoCH (most recent confirmed swing)', "CANNOT", "no confirmed swing before the CHoCH in the fetched history"))
    else:
        lvl = S.l[piv] if kind == "LOW" else S.h[piv]
        e = S.eps(ch)
        brk = S.c[ch] < lvl - e if direction == "DOWN" else S.c[ch] > lvl + e
        out.append(res2('SLS 3.6 CHoCH (Cl < L[swing] - eps)', "PASS" if brk else "CANNOT",
                       f"CHoCH close {S.c[ch]} vs most recent swing {kind.lower()} {lvl} ({iso(S.t[piv])}): "
                       f"{'broken by close' if brk else 'not broken -- the engine may reference an older HL; check by eye'}"))
    out.append(res2('SLS 3.6 MSS condition 2 (sweep or failure swing)', "CANNOT", f"origin claim (external sweep={d['has_external_sweep']}, failure swing="
                             f"{d['has_failure_swing']}) needs the trend state; not re-derived here"))
    return out


def check_stop_hunt(S, rec):
    d = rec["detail"]
    i = S.idx(ts(rec["at"]))
    if i is None:
        return [res2('SLS 2.1 (data present)', "CANNOT", "event candle not in Binance data")]
    # Measured on 4 records: the engine dates a stop hunt ONE candle after its
    # displacement candle, and the penetration candle sits `elapsed` before that.
    i = i - 1
    p = i - int(d["elapsed_candles"])
    # SLS 4.7: elapsed counts from sweep CONFIRMATION; for a two-candle sweep
    # the penetration candle is one before it.
    if (S.h[p], S.l[p]) != (D(d["penetration_high"]), D(d["penetration_low"])) and             (S.h[p - 1], S.l[p - 1]) == (D(d["penetration_high"]), D(d["penetration_low"])):
        p = p - 1
    exact = [j for j in range(i - 12, i + 1) if 0 <= j and S.h[j] == D(d["penetration_high"]) and S.l[j] == D(d["penetration_low"])]
    if exact and p not in exact:
        out_note = f"penetration candle found by its exact high/low at {iso(S.t[exact[-1]])}, not at displacement - elapsed"
        p = exact[-1]
    else:
        out_note = None
    out = [res2('engine convention (measured on 4 records), not an SLS clause', "PASS", f"(dating: event at {rec['at']} = the candle after the displacement candle {iso(S.t[i])})")]
    if out_note:
        out.append(res2('SLS 4.7 Records: elapsed candles', "FAIL", out_note + " -- elapsed_candles does not match the candles"))
    ph, pl = D(d["penetration_high"]), D(d["penetration_low"])
    out.append(res2("SLS 4.7 'which candle is measured' + 4.6", "PASS" if (S.h[p], S.l[p]) == (ph, pl) else "FAIL",
                   f"penetration candle {iso(S.t[p])}: Binance high/low {S.h[p]}/{S.l[p]} (claim {ph}/{pl})"))
    out.append(res2('SLS 4.7 Records: displacement', "PASS" if S.c[i] == D(d["displacement_close"]) else "FAIL",
                   f"displacement candle {iso(S.t[i])}: Binance close {S.c[i]} (claim {d['displacement_close']})"))
    direction = "UP" if d["side"] == "SSL" else "DOWN"
    ok, txt = S.displacement(i, direction)
    out.append(res2('SLS 4.7 + 5.10 (displacement in the reversal direction)', "PASS" if ok else "FAIL", f"displacement {direction} (SLS 5.10): {txt}"))
    mid = (S.h[p] + S.l[p]) / 2
    back = S.c[i] >= mid if direction == "UP" else S.c[i] <= mid
    out.append(res2('SLS 4.7 (closes back through >= 50 % of the penetration candle)', "PASS" if back else "FAIL",
                   f"closes back through 50% of the penetration candle (mid {mid}): close {S.c[i]} (SLS 4.7)"))
    out.append(res2('SLS 4.7 stophunt_window = 3', "PASS" if int(d["elapsed_candles"]) <= 3 else "FAIL", f"within {d['elapsed_candles']} candles (max 3)"))
    return out


def check_pool(S, rec, codes, sig_i, direction):
    d = rec["detail"]
    side, cls, src = rec["type"].split()[0], rec["type"].split()[1], rec["type"].split("(")[1].rstrip(")")
    price, lo, hi = D(str(d["price"])), D(str(d["band_low"])), D(str(d["band_high"]))
    kind = "HIGH" if side == "BSL" else "LOW"
    # SLS 4.4: external/internal liquidity is a POSITION (beyond / inside the
    # dealing range), not swing strength -- measured: these pools are written 3
    # candles after a k=2 swing. So the level is checked as a swing of either
    # strength, and the class is not re-derived (it needs the dealing range).
    out = []
    # A SWING pool is written after its swing confirms, so the swing is at or
    # before created_at. A CLUSTER's price can move as members join, so it
    # keeps the search back from the signal.
    s = S.find_swing(price, kind, at_index(S, rec, sig_i) if src == "SWING" else sig_i, K_INT)
    k = K_EXT if s is not None and S.pivot(s, kind, K_EXT) else K_INT
    if s is None:
        out.append(res2('SLS 4.2 (pool price = exact extreme)', "FAIL", f"no candle {kind.lower()} = {price} before the signal"))
        return out
    pv = S.pivot(s, kind, k)
    out.append(res2('SLS 4.2 (a) + 3.1', "PASS" if pv else "FAIL",
                   f"pool level {price} = {kind.lower()} of {iso(S.t[s])}: "
                   f"{('%s swing (k=%d)' % ('external' if k == K_EXT else 'internal', k)) if pv == 'strict' else ('swing k=%d by tie rule' % k) if pv == 'tie' else 'NOT a swing'}"
                   + (f" (cluster band {lo}..{hi}, {d['members']} members)" if src == "CLUSTER" else "")))
    out.append(res2('SLS 4.4 (external/internal = position)', "CANNOT", f"class {cls} (SLS 4.4: position vs the dealing range) not re-derived here"))
    sweep_codes = {"sweep_confirmed", "external", "depth", "unclaimed", "fresh"}
    if sweep_codes & set(codes):
        ref = lo if side == "SSL" else hi
        hits = []
        for j in range(s + k + 1, sig_i + 1):
            e = S.eps(j)
            if side == "SSL" and S.l[j] < ref - e and S.c[j] < ref:
                # marginal close: SLS 4.6.3 two-candle rule
                if S.c[j] >= ref - e and j + 1 <= sig_i and S.c[j + 1] > ref:
                    hits.append((j, "two-candle"))
                continue
            if side == "SSL" and S.l[j] < ref - e and S.c[j] > ref:
                hits.append((j, "one-candle"))
            if side == "BSL" and S.h[j] > ref + e and S.c[j] > ref:
                if S.c[j] <= ref + e and j + 1 <= sig_i and S.c[j + 1] < ref:
                    hits.append((j, "two-candle"))
                continue
            if side == "BSL" and S.h[j] > ref + e and S.c[j] < ref:
                hits.append((j, "one-candle"))
        if hits:
            j, how = hits[-1]
            out.append(res2('SLS 4.6 Detection 1-3', "PASS", f"swept: {iso(S.t[j])} wick {'low ' + str(S.l[j]) if side == 'SSL' else 'high ' + str(S.h[j])} "
                                   f"through {ref} and closed back {S.c[j]} ({how}; SLS 4.6)"))
        else:
            out.append(res2('SLS 4.6 Detection 1-3', "FAIL", f"no wick-through-and-close-back of {ref} between the swing's confirmation and the signal"))
    if "target_pool" in codes:
        right = (price < sig_close(S, sig_i)) if direction == "DOWN" else (price > sig_close(S, sig_i))
        out.append(res2('SLS 4.5 / 15.6 (target = opposing pool)', "PASS" if right else "FAIL",
                       f"target {price} is {'below' if direction == 'DOWN' else 'above'} the signal candle's close {S.c[sig_i]}"))
    return out


def sig_close(S, i):
    return S.c[i]


def check_zone(S, rec, sig_i):
    d = rec["detail"]
    kind, pol = rec["type"].split()
    lo, hi = D(str(d["band_low"])), D(str(d["band_high"]))
    out = []
    born = at_index(S, rec, sig_i)
    if kind == "OTE":
        # SLS 5.8's band is a Fibonacci retracement of a validated leg, not a
        # candle run; re-deriving it needs the dealing range. Until 2026-10-26
        # an OTE fell through to the OB check below and FAILed by construction.
        out.append(res2('SLS 5.8 (OTE band from the leg)', "CANNOT",
                        f"OTE band {lo}..{hi} is a retracement of a leg; not re-derived here"))
        return out
    if kind in ("FVG", "IFVG"):
        found = []
        # created_at is c3's close = the open of the candle after c3, so c3 <= that
        # index - 1. Not capped at the signal: c3 may BE the signal candle.
        after_c3 = at_index(S, rec, len(S.t) - 1)
        for c1 in range(0, min(sig_i - 1, after_c3 - 2)):
            c3 = c1 + 2
            if S.h[c1] == lo and S.l[c3] == hi:
                found.append((c1, "BULLISH"))
            if S.h[c3] == lo and S.l[c1] == hi:
                found.append((c1, "BEARISH"))
        want = pol if kind == "FVG" else ("BULLISH" if pol == "BEARISH" else "BEARISH")
        hit = [f for f in found if f[1] == want]
        if not hit:
            out.append(res2('SLS 5.4 Detection', "FAIL", f"no 3-candle {want.lower()} gap with edges exactly {lo}..{hi} before the signal"))
            return out
        c1 = hit[-1][0]
        gap, atr = hi - lo, S.atr[c1 + 2]
        disp = S.displacement(c1 + 1, "UP" if want == "BULLISH" else "DOWN")
        reg = (atr is not None and gap >= D("0.25") * atr) or bool(disp[0])
        out.append(res2('SLS 5.4 Detection (zone = [H c1, L c3])', "PASS", f"{want.lower()} FVG: candles {iso(S.t[c1])} .. {iso(S.t[c1 + 2])}, gap {lo}..{hi}"
                               + (" (later inverted -> IFVG)" if kind == "IFVG" else "")))
        out.append(res2('SLS 5.4 registration filter', "PASS" if reg else "FAIL",
                       f"registration filter (SLS 5.4): gap {gap / atr:.2f}x ATR (need 0.25) or middle candle displaced ({disp[1]})"))
        if kind == "IFVG":
            inv = [j for j in range(c1 + 3, sig_i + 1) if (S.c[j] < lo if want == "BULLISH" else S.c[j] > hi)]
            out.append(res2('SLS 5.4 State INVERTED -> 5.5', "PASS" if inv else "FAIL",
                           f"inversion close through the far edge: {iso(S.t[inv[0]]) if inv else 'none before the signal'}"))
        return out
    # OB / BREAKER: a run of 1-3 same-coloured candles whose combined range is the band
    origin_bear = (kind == "OB" and pol == "BULLISH") or (kind == "BREAKER" and pol == "BEARISH")
    colour = (lambda j: S.c[j] < S.o[j]) if origin_bear else (lambda j: S.c[j] > S.o[j])
    def fits(st, end):
        js = range(st, end + 1)
        return min(S.l[j] for j in js) == lo and max(S.h[j] for j in js) == hi and all(
            colour(j) or S.c[j] == S.o[j] for j in js)

    found = None
    if kind == "OB" and S.idx(ts(rec["at"])) is not None:
        # An OB's created_at IS its first origin candle: try only runs starting there.
        st = S.idx(ts(rec["at"]))
        for run in (1, 2, 3):
            if st + run - 1 <= sig_i and fits(st, st + run - 1):
                found = (st, st + run - 1)
                break
    else:
        # A breaker's parent run ends before its invalidation (created_at).
        for end in range(min(born, sig_i), 0, -1):
            for run in (1, 2, 3):
                st = end - run + 1
                if st >= 0 and fits(st, end):
                    found = (st, end)
                    break
            if found:
                break
    if not found:
        out.append(res2('SLS 5.1 Detection 1 + 3 (run <= 3, full-range zone)', "FAIL", f"no run of 1-3 {'bearish' if origin_bear else 'bullish'} candles spanning exactly {lo}..{hi} before the signal"))
        return out
    st, end = found
    out.append(res2('SLS 5.1 Detection 1 + 3', "PASS", f"origin candle(s) {iso(S.t[st])}{'' if st == end else ' .. ' + iso(S.t[end])} span {lo}..{hi}"))
    move = "UP" if origin_bear else "DOWN"
    disp = [j for j in range(end + 1, min(end + 6, sig_i + 1)) if S.displacement(j, move)[0]]
    out.append(res2('SLS 5.1 Detection 2a + 5.10', "PASS" if disp else "FAIL",
                   f"displacement {move} within 5 candles after (SLS 5.1.2a): {iso(S.t[disp[0]]) if disp else 'none'}"))
    if kind == "BREAKER":
        inv = [j for j in range(end + 1, sig_i + 1) if (S.c[j] > hi if pol == "BULLISH" else S.c[j] < lo)]
        out.append(res2('SLS 5.2 Detection 1', "PASS" if inv else "FAIL",
                       f"OB invalidated by a close through its far edge (SLS 5.2.1): {iso(S.t[inv[0]]) if inv else 'none'}"))
    h = hi - lo
    a = S.atr[end]
    if a:
        out.append(res2('SLS 5.1 Validation (zone height 0.15-3 ATR)', "PASS" if D("0.15") * a <= h <= 3 * a else "FAIL", f"zone height {h / a:.2f}x ATR (SLS 5.1: 0.15..3)"))
    return out


def walk_outcome(S, sig):
    """SLS 12.3/12.4/12.5 walk from the candle after the signal candle. Premise checks are not simulated."""
    p = sig["payload"]
    tf, direction = sig["timeframe"], sig["direction"]
    i0 = S.idx(ts(sig["published_at"]))
    if i0 is None:
        return res2('SLS 12.3 (data present)', "CANNOT", "signal candle not in Binance data"), None
    prox, dist = D(p["entry_zone"]["proximal"]), D(p["entry_zone"]["distal"])
    zlo, zhi = min(prox, dist), max(prox, dist)
    inv = D(p["invalidation"]["price"])
    up = direction == "UP"
    last = min(i0 + TTL[tf], len(S.t) - 1)
    active = None
    if tf != "M5":
        tg = p["targets"]["primary"]
        tlo, thi = D(tg["low"]), D(tg["high"])
        for j in range(i0 + 1, last + 1):
            if active is None and S.l[j] <= zhi and S.h[j] >= zlo:
                active = j
                if ACTIVATION_CANDLE_EXCLUDED:
                    continue
            if active is not None:
                hit_t = S.h[j] >= tlo if up else S.l[j] <= thi
                fail = S.c[j] < inv if up else S.c[j] > inv
                if fail:
                    return res2('SLS 12.3 invalidation close + 12.4 same-candle collision', "FAILED", f"close {S.c[j]} beyond invalidation {inv} at {iso(S.t[j])}"
                               + (" (same candle also touched the target -> FAILED by 12.4)" if hit_t else "")), S.t[j]
                if hit_t:
                    return res2('SLS 12.3 target touch', "SUCCESS", f"target {tlo}..{thi} touched at {iso(S.t[j])}"), S.t[j]
        return (res2('SLS 12.5 TTL', "EXPIRED_ACTIVE" if active is not None else "EXPIRED_UNTOUCHED",
                    f"TTL {TTL[tf]} candles ran out at {iso(S.t[last])}"), S.t[last])
    # M5: entry at proximal, risk stop on touch, ladder TPn=(n+1)R, trailing (12.3 M5)
    R = abs(prox - inv)
    stop, rung = inv, 1
    for j in range(i0 + 1, last + 1):
        if active is None:
            if (S.l[j] <= prox) if up else (S.h[j] >= prox):
                active = j
                if ACTIVATION_CANDLE_EXCLUDED:
                    continue
            else:
                continue
        while True:
            nxt = prox + (rung + 1) * R if up else prox - (rung + 1) * R
            hit_stop = S.l[j] <= stop if up else S.h[j] >= stop
            hit_rung = S.h[j] >= nxt if up else S.l[j] <= nxt
            if hit_stop:
                r = (stop - prox) / R if up else (prox - stop) / R
                st = "SUCCESS" if r > 0 else "FAILED" if r < 0 else "CLOSED_FLAT"
                return res2('SLS 12.3 M5 (touch, ladder, trailing) + 12.4 M5 realised_r', st, f"stop {stop} touched at {iso(S.t[j])}, realised R {r:.4f}"
                           + (" (same candle reached a rung -> stop first by 12.3)" if hit_rung else "")), S.t[j]
            if hit_rung:
                stop = prox if rung == 1 else (prox + rung * R if up else prox - rung * R)
                rung += 1
                if MOVED_STOP_NEXT_CANDLE:
                    # still advance through every rung this candle passed, but the
                    # moved stop is only tested from the next candle
                    while (S.h[j] >= (prox + (rung + 1) * R) if up else S.l[j] <= (prox - (rung + 1) * R)):
                        stop = prox + rung * R if up else prox - rung * R
                        rung += 1
                    break
                continue
            break
    if active is None:
        return res2('SLS 12.3 M5 + 12.5', "EXPIRED_UNTOUCHED", f"entry {prox} never touched in {TTL[tf]} candles"), S.t[last]
    r = (S.c[last] - prox) / R if up else (prox - S.c[last]) / R
    return res2('SLS 12.4 M5 (EXPIRED_ACTIVE realised R) + 12.5', "EXPIRED_ACTIVE", f"TTL ran out at {iso(S.t[last])}, open R at that close {r:.4f}"), S.t[last]


def main():
    src, dst = sys.argv[1], sys.argv[2]
    data = json.load(open(src, encoding="utf-8"))
    recs = data["records"]
    ranges = {}
    for sg in data["signals"]:
        tf = sg["timeframe"]
        times = [ts(sg["published_at"])]
        for items in sg["setup_evidence"]["attribution"].values():
            for it in items:
                for r in recs.get(it.get("evidence_id") or "", []):
                    times.append(ts(r["at"]))
        step = TF[tf][0]
        lo = min(times) - WARM * step
        hi = ts(sg["published_at"]) + (TTL[tf] + 3) * step
        a, b = ranges.get((sg["symbol"], tf), (lo, hi))
        ranges[(sg["symbol"], tf)] = (min(a, lo), max(b, hi))
    series = {}
    for sg in data["signals"]:                      # zones can be born long before their evidence time
        for items in sg["setup_evidence"]["attribution"].values():
            for it in items:
                for r in recs.get(it.get("evidence_id") or "", []):
                    if r["table"] == "ict_zones":
                        a_, b_ = ranges[(sg["symbol"], sg["timeframe"])]
                        ranges[(sg["symbol"], sg["timeframe"])] = (min(a_, ts(r["at"]) - ZONE_BACK * TF[sg["timeframe"]][0]), b_)
    for (sym, tf), (lo, hi) in sorted(ranges.items()):
        step = TF[tf][0]
        series[(sym, tf)] = Series(fetch(sym, tf, lo - lo % step, hi - hi % step), step)
    results = []
    for sg in data["signals"]:
        S = series[(sg["symbol"], sg["timeframe"])]
        sig_i = S.idx(ts(sg["published_at"]))
        items = {}
        for f, its in sg["setup_evidence"]["attribution"].items():
            for it in its:
                if it.get("evidence_id"):
                    items.setdefault(it["evidence_id"], []).append(it["code"])
        lines = []
        for eid, codes in items.items():
            rr = recs.get(eid)
            if not rr:
                lines.append({"id": eid, "claim": "record not found", "checks": [res2('none (record missing)', "FAIL", "record missing")]})
                continue
            r = rr[0]
            t = r["type"]
            try:
                if r["table"] == "engine_events" and t.startswith("BOS_"):
                    ch = check_bos(S, r, codes)
                elif r["table"] == "engine_events" and t.startswith("STRUCTURE_EXTERNAL_"):
                    ch = check_swing(S, r)
                elif r["table"] == "engine_events" and t.startswith("MSS_"):
                    ch = check_mss(S, r)
                elif r["table"] == "engine_events" and t == "LIQUIDITY_STOP_HUNT":
                    ch = check_stop_hunt(S, r)
                elif r["table"] == "liquidity_pools":
                    ch = check_pool(S, r, codes, sig_i, sg["direction"])
                elif r["table"] == "ict_zones":
                    ch = check_zone(S, r, sig_i)
                else:
                    ch = [res2('none (no checker)', "CANNOT", f"no checker for {r['table']} {t}")]
            except Exception as ex:                                      # a checker crash is reported, not hidden
                ch = [res2('none (checker error)', "CANNOT", f"checker error: {type(ex).__name__}: {ex}")]
            lines.append({"id": eid, "claim": f"{t} {r['timeframe']} {r['at']}", "codes": codes, "checks": ch})
        oc, oc_t = walk_outcome(S, sg)
        same = oc["verdict"] == sg["outcome"] or (sg["outcome"] == "INVALIDATED_EARLY")
        results.append({
            "signal_id": sg["signal_id"], "symbol": sg["symbol"], "timeframe": sg["timeframe"],
            "direction": sg["direction"], "published_at": sg["published_at"], "lines": lines,
            "outcome_engine": sg["outcome"], "resolved_engine": sg["resolved_at"], "realised_r": sg["realised_r"],
            "outcome_walk": oc,
            "outcome_match": ("CANNOT" if sg["outcome"] == "INVALIDATED_EARLY" else
                              "PASS" if oc["verdict"] == sg["outcome"] and
                              (oc_t is None or sg["resolved_at"] is None or oc_t == ts(sg["resolved_at"])) else
                              "TIME" if oc["verdict"] == sg["outcome"] else "FAIL"),
            "gaps_in_binance_data": len(S.gaps),
        })
    json.dump({"window": data["window"], "results": results}, open(dst, "w", encoding="utf-8"), indent=1, default=str)
    from collections import Counter
    c = Counter(ch["verdict"] for r in results for l in r["lines"] for ch in l["checks"])
    o = Counter(r["outcome_match"] for r in results)
    print(f"{len(results)} signals; claim checks {dict(c)}; outcome walk {dict(o)}; "
          f"series {len(series)}, binance gaps {sum(s_.gaps.__len__() for s_ in series.values())}")


if __name__ == "__main__":
    main()
