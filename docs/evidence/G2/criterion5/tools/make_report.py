"""Render independent_check.py's JSON as one local HTML report (Roman Urdu headings).

    python make_report.py check.json audit.json report.html "title"
"""
import html
import json
import sys
from collections import Counter

chk, aud, dst, title = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
d = json.load(open(chk, encoding="utf-8"))
a = json.load(open(aud, encoding="utf-8"))
sig = {s["signal_id"]: s for s in a["signals"]}
R = d["results"]
allc = Counter(ch["verdict"] for r in R for l in r["lines"] for ch in l["checks"])
oc = Counter(r["outcome_match"] for r in R)
disp = [r for r in R if any(ch["verdict"] == "FAIL" and ch["text"].startswith("displaced") for l in r["lines"] for ch in l["checks"])]
other_fail = [(r, l, ch) for r in R for l in r["lines"] for ch in l["checks"]
              if ch["verdict"] == "FAIL" and not ch["text"].startswith("displaced")]
COL = {"PASS": "ok", "FAIL": "bad", "CANNOT": "unk", "TIME": "unk"}


def badge(v):
    return f"<span class='b {COL.get(v, 'unk')}'>{v}</span>"


cards = []
for n, r in enumerate(R, 1):
    s = sig[r["signal_id"]]
    rows = []
    for l in r["lines"]:
        for ch in l["checks"]:
            rows.append(f"<tr><td>{badge(ch['verdict'])}</td><td class=c>{html.escape(l['claim'])}</td>"
                        f"<td>{html.escape(ch['text'])}<div class=muted>{html.escape(ch.get('clause') or '')}</div></td></tr>")
    rows.append(f"<tr class=oc><td>{badge(r['outcome_match'])}</td><td class=c>natija</td><td>engine: "
                f"{r['outcome_engine']} @ {r['resolved_engine']} · Binance walk: {html.escape(r['outcome_walk']['verdict'])} — "
                f"{html.escape(r['outcome_walk']['text'])}<div class=muted>{html.escape(r['outcome_walk'].get('clause') or '')}</div></td></tr>")
    nf = sum(1 for l in r["lines"] for ch in l["checks"] if ch["verdict"] == "FAIL")
    cards.append(f"<details class=card{' open' if nf else ''}><summary><b>#{n} {r['symbol']} {r['timeframe']} "
                 f"{r['direction']}</b> · {r['published_at']} · {s['archetype']} · conf {s['payload'].get('confidence')} · "
                 f"{'FAIL ' + str(nf) if nf else 'koi FAIL nahi'}</summary><table>{''.join(rows)}</table></details>")

page = f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width, initial-scale=1">
<title>Independent Audit Check</title><style>
:root {{ --bg:#fafaf7; --fg:#1d1d1b; --muted:#6b6b66; --card:#fff; --line:#e3e2dc; --ok:#2e7d4f; --bad:#b3362c; --unk:#8a6d1d; --acc:#2f5d8a; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#16171a; --fg:#e8e6e1; --muted:#9a988f; --card:#1f2024; --line:#33343a; --ok:#5cc28a; --bad:#ef7a6f; --unk:#d9b44a; --acc:#7fb0e0; }} }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui,sans-serif; margin:0; padding:16px; }}
main {{ max-width:1000px; margin:0 auto; }} h1 {{ font-size:22px; }} h2 {{ font-size:18px; margin-top:28px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 14px; margin:10px 0; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }} td {{ border-top:1px solid var(--line); padding:5px 4px; vertical-align:top; }}
td.c {{ color:var(--muted); width:220px; }} .b {{ font-weight:600; font-size:12px; }} .ok {{ color:var(--ok); }} .bad {{ color:var(--bad); }} .unk {{ color:var(--unk); }}
.big {{ font-size:20px; font-weight:600; }} .muted {{ color:var(--muted); font-size:13px; }}
@media (max-width:640px) {{ td {{ display:block; }} td.c {{ width:auto; }} }}
</style></head><body><main>
<h1>{html.escape(title)}</h1>
<div class=card>
<p>Har signal ka har daawa <b>Binance ki apni candles</b> par dobara jaancha gaya. Ye candles hamare database se nahi, Binance ke public API se aayi hain. Jaanch ka code alag hai: engine ka koi code istemal nahi hua, sirf SLS ki likhi hui tareef.</p>
<p class=big><span class=ok>{allc['PASS']} PASS</span> · <span class=bad>{allc['FAIL']} FAIL</span> · <span class=unk>{allc['CANNOT']} CANNOT</span> &nbsp;|&nbsp; natije: <span class=ok>{oc['PASS']} match</span>, <span class=unk>{oc.get('CANNOT', 0)} jaanch se bahar</span>{', <span class=bad>' + str(oc['FAIL']) + ' farq</span>' if oc.get('FAIL') else ''}</p>
<p class=muted>CANNOT ka matlab: ye script us daawe ka faisla nahi kar sakta, kyunke trend state ya dealing range ka hisaab is mein nahi hai. Ye "PASS" nahi hai. Window {d['window']['start']} → {d['window']['end']}, {len(R)} signals.</p>
</div>

<h2>1. Asal finding: "displaced" ki do tareefen ({len(disp)} signals)</h2>
<div class=card>
<p><b>SLS §3.5</b> kehta hai: BOS "displaced" hai jab <b>break wali candle ya us se pichli candle</b> §5.10 displacement ki shart poori kare. Shart ye hai: body 20 candles ki average se 2 guna, range 1.5×ATR, aur close candle ke kinare ke 25% mein.</p>
<p><b>Engine</b> yehi cheez <b>impulse leg (§7.5)</b> se parakhta hai, jo §3.5 se alag tareeqa hai.</p>
<p>Natija: {len(disp)} signals mein engine ne "displaced" ke <b>+18 points</b> diye, jabke §3.5 ki tareef poori nahi hoti. In mein se {sum(1 for r in disp if sig[r['signal_id']]['archetype'] == 'A3')} <b>A3</b> hain. A3 ki apni shart hi "displaced BOS" hai (§8.6).</p>
<p class=muted>Andaza, tasdeeq nahi kiya: F1 ka wazan 0.20 hai, to +18 hatane se confidence taqreeban 3.6 ghatta. Floors aur synergy ko engine ke baghair dobara nahi gina gaya.</p>
<p><b>Ye faisla aap ka hai:</b> kaunsi tareef sahi hai? Engine ko §3.5 ke mutabiq badla jaye, ya SLS mein leg wali tareef likh di jaye? Aap ki haan ke baghair code nahi badlega.</p>
</div>

<h2>2. Doctrine ke dhundle kone (engine ghalat nahi, SLS saaf nahi)</h2>
<div class=card><ul>
<li><b>Entry wali candle:</b> engine us candle par stop ya target nahi ginta jis par entry pehli dafa chhui gayi. Ye agli candle se ginta hai. M5 par jab stop aage khiskta hai, to naya stop bhi agli candle se lagta hai. SLS §12.3 ye nahi batata. Is qaide se mera hisaab <b>33 mein se 33</b> natijon se, waqt samet, match hua. Is ke baghair 30 hi match hote.</li>
<li><b>Barabar highs/lows (tie) ka qaida, §3.1:</b> ek jagah (ZECUSDT M5, 15 Oct) pichli candles thodi oonchi thin, magar ε ke andar. Engine aur meri reading alag hui. SLS isay saaf nahi karta.</li>
</ul></div>

<h2>3. Dekhne layak baatein (FAIL nahi)</h2>
<div class=card><ul>
<li>Stop hunt ka waqt engine displacement candle ke <b>ek candle baad</b> likhta hai. Ye har record mein ek jaisa hai.</li>
<li>Ek breaker (PUMPUSDT M15, 15 Oct) ka parent OB <b>26 Sep</b> ka tha, yani 19 din purana. Jo OB chhua gaya ho wo §5.1 ke mutabiq expire nahi hota, is liye ye jaiz ho sakta hai.</li>
<li>"EXTERNAL" pools aksar internal (k=2) swings se bane hain. §4.4 external/internal ko dealing range mein jagah se tay karta hai, is liye ye jaiz ho sakta hai. Isay yahan dobara hisaab nahi kiya gaya.</li>
</ul>
{('<p><b>Baqi FAIL:</b></p><ul>' + ''.join(f"<li>{html.escape(r['symbol'])} {r['timeframe']}: {html.escape(ch['text'])}</li>" for r, l, ch in other_fail) + '</ul>') if other_fail else '<p>Is ke ilawa koi FAIL nahi.</p>'}
</div>

<h2>4. Har signal</h2>
{''.join(cards)}
<p class=muted>Ye jaanch insaan ka audit nahi hai. Roadmap criterion 5 ka audit developer ke haath se chart par hona hai. Is jaanch ko us ki jagah maana jaye ya nahi, ye owner ka faisla hai.</p>
</main></body></html>"""
open(dst, "w", encoding="utf-8").write(page)
print(dst, dict(allc), dict(oc), "displaced signals", len(disp), "other fails", len(other_fail))
