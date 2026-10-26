"""Build the G2 criterion 5 hand-audit sheet (one local HTML file) from audit_data.py's JSON.

    python make_sheet.py audit.json sheet.html "title"

The sheet is for the owner's own chart reading. It states what the engine
claims and where to look; it never says whether the claim is right. Marks are
kept in the browser (localStorage, best effort) and exported as CSV.
"""
import html
import json
import sys
from datetime import datetime, timedelta

src, dst, title = sys.argv[1], sys.argv[2], sys.argv[3]
d = json.load(open(src, encoding="utf-8"))
records = d["records"]

TV_INTERVAL = {"M5": "5", "M15": "15", "H1": "60", "H4": "240", "D1": "D"}
HIGHER = {"M5": "M15", "M15": "H1", "H1": "H4", "H4": "D1", "D1": "D1"}
REASON = {
    "A1": "external sweep hua, phir trend palta (shift), aur price us shift ke origin zone par wapas aayi",
    "A2": "breaker bana aur pehli dafa wapas aane par usne price ko roka",
    "A3": "trend chal raha tha, ek tez (displaced) break hua, phir price wapas zone mein aayi",
    "A4": "trend ki taraf ek tez move ka gap (FVG) bana, aur price pehli dafa us gap ko chhooti",
}
OUTCOME = {
    "SUCCESS": "target tak pohncha",
    "FAILED": "stop toota",
    "EXPIRED_ACTIVE": "waqt khatam, trade khula tha",
    "EXPIRED_UNTOUCHED": "waqt khatam, entry kabhi chhui nahi",
    "INVALIDATED_EARLY": "entry se pehle radd",
    None: "abhi khula",
}
ZONE_HELP = {
    "FVG": "FVG = 3 candles mein beech ki candle itni tez ke pehli aur teesri candle ki wicks nahi milti; beech ka khali hissa zone hai",
    "IFVG": "IFVG = ek FVG jise price ne close se paar kar diya, ab ulti taraf ka zone",
    "OB": "OB (order block) = tez move se theek pehle ki aakhri ulti rang ki candle",
    "BREAKER": "Breaker = ek OB jo toot gaya; ab wohi jagah ulti taraf ka zone",
}


def num(x):
    if x is None:
        return "-"
    s = f"{float(x):.10f}".rstrip("0").rstrip(".")
    return s


def pkt(iso):
    if not iso:
        return "-"
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (t + timedelta(hours=5)).strftime("%Y-%m-%d %H:%M PKT")


def utc(iso):
    if not iso:
        return "-"
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M UTC")


def tv(symbol, tf):
    return (f"https://www.tradingview.com/chart/?symbol=BINANCE%3A{symbol}"
            f"&interval={TV_INTERVAL.get(tf, '60')}")


def question(rec, codes, direction):
    t, det = rec["type"], rec["detail"] or {}
    at = f"{utc(rec['at'])} ({pkt(rec['at'])})"
    tfx = rec["timeframe"]
    if rec["table"] == "engine_events":
        if t.startswith("BOS_"):
            side = "neeche" if t.endswith("DOWN") else "upar"
            q = (f"<b>BOS ({tfx})</b>: candle {at} ne pichla swing {det.get('swing_kind','')} "
                 f"<b>{num(det.get('swing_price'))}</b> ko <b>close</b> se {side} toda? "
                 f"(candle close = {num(det.get('candle_close'))})")
            if "displaced" in codes:
                q += "<br>&nbsp;&nbsp;+ <b>displaced</b>: kya ye todne wali move tez aur bari candles se thi (aam candles se kaafi lambi)?"
            return q
        if t.startswith("MSS_"):
            return (f"<b>MSS ({tfx})</b>: {at} ke aas paas trend palta? Pehle CHoCH (ulti taraf "
                    f"ka pehla structure break), phir usi taraf follow-through.")
        if t.startswith("STRUCTURE_EXTERNAL_"):
            return (f"<b>External swing {det.get('label','')} ({tfx})</b>: price <b>{num(det.get('price'))}</b> "
                    f"par ek bara, saaf swing {('high' if det.get('kind') == 'HIGH' else 'low')} hai? "
                    f"(engine ne isay {at} par confirm kiya — swing khud is se kuch candles pehle ka hoga)")
        if t == "LIQUIDITY_STOP_HUNT":
            return (f"<b>Stop hunt ({tfx})</b>: {at} ke aas paas sweep ke foran baad ulti taraf "
                    f"ek tez move (displacement) aayi? (displacement close = {num(det.get('displacement_close'))})")
        return f"<b>{html.escape(t)} ({tfx})</b>: {at} — chart par ye waqia nazar aata hai?"
    if rec["table"] == "liquidity_pools":
        side = det and t.split()[0]
        where = "upar wale highs (BSL = buy-side liquidity)" if side == "BSL" else "neeche wale lows (SSL = sell-side liquidity)"
        lvl = num(det.get("price"))
        if "target_pool" in codes and len(codes) == 1:
            return (f"<b>Target pool ({tfx})</b>: level <b>{lvl}</b> par {where} hain — yani aisi jagah jahan "
                    f"price ja sakti hai? ({det.get('members')} swing{'s' if (det.get('members') or 1) > 1 else ''})")
        return (f"<b>Sweep ({tfx})</b>: level <b>{lvl}</b> ({where}, {t.split(' ', 1)[1]}) ko signal se "
                f"<b>pehle</b> kisi candle ki wick ne paar kiya, aur candle wapas andar band hui?")
    if rec["table"] == "ict_zones":
        kind = t.split()[0]
        pol = "bullish (kharidne wala)" if "BULLISH" in t else "bearish (bechne wala)"
        return (f"<b>Zone {html.escape(t)} ({tfx}, grade {det.get('grade')})</b>: "
                f"<b>{num(det.get('band_low'))} – {num(det.get('band_high'))}</b> chart par sahi {pol} "
                f"{kind} hai?<br><span class=help>{ZONE_HELP.get(kind, '')}</span>")
    return f"<b>{html.escape(t)} ({tfx})</b>: {at}"


# --- suggested spot sample: one per (timeframe, outcome), then spread to 10 --------
sigs = d["signals"]
groups = {}
for i, s in enumerate(sigs):
    groups.setdefault((s["timeframe"], s["outcome"]), []).append(i)
sample = [v[0] for v in groups.values()]
rest = [i for i in range(len(sigs)) if i not in sample]
step = max(1, len(rest) // max(1, 10 - len(sample)))
sample += rest[::step][: max(0, 10 - len(sample))]
sample = set(sample)

cards = []
for n, s in enumerate(sigs, 1):
    p = s["payload"] or {}
    ez, inv, tg = p.get("entry_zone") or {}, p.get("invalidation") or {}, (p.get("targets") or {}).get("primary") or {}
    att = (s.get("setup_evidence") or {}).get("attribution") or {}
    by_id = {}
    for f, items in att.items():
        for it in items:
            if it.get("evidence_id"):
                by_id.setdefault(it["evidence_id"], []).append((f, it["code"], it["points"]))
    rows = []
    for eid, codes in by_id.items():
        recs = records.get(eid) or []
        cnames = [c for _, c, _ in codes]
        pts = ", ".join(f"{f} {c} +{num(pt)}" for f, c, pt in codes)
        if not recs:
            q = f"<b>record nahi mila</b> (id {eid[:12]}…) — ye khud ek finding hai"
        else:
            q = question(recs[0], cnames, s["direction"])
        rows.append((f"{s['signal_id'][:12]}:{eid[:12]}", q, pts))
    dirw = "UP — long (kharidna)" if s["direction"] == "UP" else "DOWN — short (bechna)"
    r = s.get("realised_r")
    rtxt = f", R = {num(r)}" if r is not None else ""
    star = '<span class=star>★ pehle ye (sample)</span>' if (n - 1) in sample else ""
    sid = s["signal_id"][:12]
    rowhtml = "".join(
        f"<tr data-k='{k}'><td>{q}<div class=pts>points: {html.escape(pt)}</div></td>"
        f"<td class=marks>{''.join(f'<button data-v={v}>{lab}</button>' for v, lab in (('ok','✓ sahi'),('bad','✗ ghalat'),('unk','? pata nahi')))}"
        f"<input placeholder='note'></td></tr>" for k, q, pt in rows)
    cards.append(f"""
<section class=card id=s{n} data-sid='{sid}'>
 <h2>#{n} {s['symbol']} {s['timeframe']} — {dirw} {star}</h2>
 <p class=meta>Signal: <b>{utc(s['published_at'])}</b> ({pkt(s['published_at'])}) · {s['archetype']} · grade {s['grade']} · confidence {num(p.get('confidence'))} · {s['algo_version']}</p>
 <p>Machine ki wajah: {REASON.get(s['archetype'], '')}.</p>
 <p class=trade>Entry zone <b>{num(ez.get('proximal'))} – {num(ez.get('distal'))}</b> · Stop <b>{num(inv.get('price'))}</b> · Target <b>{num(tg.get('low'))}{'' if tg.get('low') == tg.get('high') else ' – ' + num(tg.get('high'))}</b></p>
 <p class=meta>Natija: {OUTCOME.get(s.get('outcome'), s.get('outcome'))}{rtxt}</p>
 <p><a href="{tv(s['symbol'], s['timeframe'])}" target=_blank rel=noopener>Chart kholo ({s['timeframe']})</a> ·
    <a href="{tv(s['symbol'], HIGHER[s['timeframe']])}" target=_blank rel=noopener>bara timeframe ({HIGHER[s['timeframe']]})</a></p>
 <table>{rowhtml}
  <tr data-k='{sid}:overall' class=overall><td><b>Poora signal</b>: aap ki apni chart reading ke hisaab se kya ye ICT setup sach mein tha?</td>
   <td class=marks><button data-v=ok>✓ sahi</button><button data-v=bad>✗ ghalat</button><button data-v=unk>? pata nahi</button><input placeholder='note'></td></tr>
 </table>
</section>""")

page = f"""<!doctype html><html lang=ur><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1"><title>G2 Audit Sheet</title>
<style>
:root {{ --bg:#fafaf7; --fg:#1d1d1b; --muted:#6b6b66; --card:#fff; --line:#e3e2dc; --acc:#2f5d8a; --ok:#2e7d4f; --bad:#b3362c; --unk:#8a6d1d; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#16171a; --fg:#e8e6e1; --muted:#9a988f; --card:#1f2024; --line:#33343a; --acc:#7fb0e0; --ok:#5cc28a; --bad:#ef7a6f; --unk:#d9b44a; }} }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; margin:0; padding:16px; }}
main {{ max-width:960px; margin:0 auto; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; margin:14px 0; }}
h1 {{ font-size:22px; }} h2 {{ font-size:17px; margin:0 0 6px; }}
.meta, .pts, .help {{ color:var(--muted); font-size:13px; }}
.trade {{ font-variant-numeric: tabular-nums; }}
table {{ width:100%; border-collapse:collapse; }} td {{ border-top:1px solid var(--line); padding:8px 4px; vertical-align:top; }}
.marks {{ width:230px; }} .marks input {{ width:100%; margin-top:4px; box-sizing:border-box; background:var(--bg); color:var(--fg); border:1px solid var(--line); border-radius:4px; padding:3px; }}
button {{ border:1px solid var(--line); background:var(--bg); color:var(--fg); border-radius:5px; padding:3px 7px; margin:0 3px 3px 0; cursor:pointer; font-size:13px; }}
button.on[data-v=ok] {{ background:var(--ok); color:#fff; }} button.on[data-v=bad] {{ background:var(--bad); color:#fff; }} button.on[data-v=unk] {{ background:var(--unk); color:#fff; }}
.star {{ color:var(--acc); font-size:13px; margin-left:6px; }}
.overall td {{ background:color-mix(in srgb, var(--acc) 8%, transparent); }}
a {{ color:var(--acc); }}
#bar {{ position:sticky; top:0; background:var(--bg); padding:8px 0; border-bottom:1px solid var(--line); z-index:1; }}
@media (max-width:640px) {{ .marks {{ width:auto; }} td {{ display:block; }} }}
</style></head><body><main>
<h1>{html.escape(title)}</h1>
<div class=card>
<p><b>Ye kya hai:</b> G2 criterion 5, yani 7 din ke signals aap khud chart par check karte ho. Har signal ke neeche machine ka <b>daawa</b> likha hai ("yahan BOS hua", "ye zone hai"). Aap chart khol kar dekho ke daawa sach hai ya nahi. Ye sheet sirf daawa batati hai, faisla aap ka hai.</p>
<ol>
<li><b>"Chart kholo"</b> dabao: TradingView par wohi coin aur timeframe khulega (Binance spot).</li>
<li>Signal ka waqt chart par dhoondo. Waqt UTC aur PKT (UTC+5) dono mein likha hai. Candle ka waqt us ke <b>khulne</b> ka waqt hai, jaise TradingView dikhata hai.</li>
<li>Har line par <b>✓ sahi</b>, <b>✗ ghalat</b> ya <b>? pata nahi</b> dabao. Zaroorat ho to note likho.</li>
<li>Aakhir mein "Poora signal" wali line bharo.</li>
<li>Kaam khatam ho to <b>CSV nikalo</b> dabao, aur wo file mujhe bhej do.</li>
</ol>
<p class=meta>"★ pehle ye" wale {len(sample)} signals ek mashwara hain: har timeframe aur har natije ka kam az kam ek. Kitne check karne hain, ye aap ka faisla hai. Zone aur pool ke saath jo waqt hai wo database mein record likhne ka waqt hai, candle ka nahi. Level ko price se dhoondo. Window: {d['window']['start']} → {d['window']['end']} · {len(sigs)} signals · evidence {d['ids_found']}/{d['ids_total']} mila.</p>
</div>
<div id=bar><button id=csv>CSV nikalo</button> <span id=count class=meta></span></div>
{''.join(cards)}
</main>
<script>
const KEY = 'g2audit:' + {json.dumps(d['window']['start'] + '|' + d['window']['end'])};
let marks = {{}};
try {{ marks = JSON.parse(localStorage.getItem(KEY) || '{{}}'); }} catch (e) {{ marks = {{}}; }}
function save() {{ try {{ localStorage.setItem(KEY, JSON.stringify(marks)); }} catch (e) {{}} count(); }}
function count() {{
  const rows = document.querySelectorAll('tr[data-k]'); let done = 0;
  rows.forEach(r => {{ if ((marks[r.dataset.k] || {{}}).v) done++; }});
  document.getElementById('count').textContent = done + ' / ' + rows.length + ' lines marked';
}}
document.querySelectorAll('tr[data-k]').forEach(tr => {{
  const k = tr.dataset.k, m = marks[k] || {{}}, inp = tr.querySelector('input');
  tr.querySelectorAll('button').forEach(b => {{
    if (b.dataset.v === m.v) b.classList.add('on');
    b.onclick = () => {{
      tr.querySelectorAll('button').forEach(x => x.classList.remove('on'));
      b.classList.add('on'); marks[k] = Object.assign(marks[k] || {{}}, {{ v: b.dataset.v }}); save();
    }};
  }});
  if (m.note) inp.value = m.note;
  inp.oninput = () => {{ marks[k] = Object.assign(marks[k] || {{}}, {{ note: inp.value }}); save(); }};
}});
document.getElementById('csv').onclick = () => {{
  const out = [['signal', 'line', 'claim', 'verdict', 'note']];
  document.querySelectorAll('section.card').forEach(sec => {{
    const h = sec.querySelector('h2').textContent;
    sec.querySelectorAll('tr[data-k]').forEach(tr => {{
      const m = marks[tr.dataset.k] || {{}};
      out.push([h, tr.dataset.k, tr.cells[0].innerText.replace(/\\s+/g, ' '), m.v || '', m.note || '']);
    }});
  }});
  const csv = out.map(r => r.map(c => '"' + String(c).replace(/"/g, '""') + '"').join(',')).join('\\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob(['\\ufeff' + csv], {{ type: 'text/csv' }}));
  a.download = 'g2_audit_marks.csv'; a.click();
}};
count();
</script></body></html>"""
open(dst, "w", encoding="utf-8").write(page)
print(f"{dst}: {len(sigs)} signals, {sum(r.count('<tr data-k') for r in cards)} lines, sample {sorted(i + 1 for i in sample)}")
