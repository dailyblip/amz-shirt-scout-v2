from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"


def fmt_num(v):
    if v is None:
        return "—"
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v)


def fmt_pct(v):
    if v is None:
        return "—"
    return f"{'+' if v > 0 else ''}{v:.1f}%"


def fmt_price(v):
    return f"${v:,.2f}" if isinstance(v, (int, float)) else "—"


def embed_json(payload: dict) -> str:
    """Safe to drop inside a <script> block."""
    return (
        json.dumps(payload, ensure_ascii=False)
        .replace("</", "<\\/")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def product_row(i: int, p: dict, show_deltas: bool) -> str:
    image = p.get("image_url") or ""
    img = (f'<img src="{html.escape(image)}" alt="" loading="lazy">'
           if image else '<div class="ph">TEE</div>')
    conf = p.get("merch_confidence") or "—"
    conf_class = f"conf conf-{conf.lower()}"
    asin = html.escape(p["asin"])


    deltas = ""
    if show_deltas:
        d24, d7 = p.get("change_24h_pct"), p.get("change_7d_pct")
        deltas = (
            f'<td class="delta" data-value="{d24 if d24 is not None else ""}">{fmt_pct(d24)}</td>'
            f'<td class="delta" data-value="{d7 if d7 is not None else ""}">{fmt_pct(d7)}</td>'
        )

    return f"""
        <tr data-asin="{asin}">
          <td class="rank">{i}</td>
          <td class="thumb">{img}</td>
          <td class="product">
            <a href="{html.escape(p.get('amazon_url', ''))}" target="_blank" rel="noopener">{html.escape(p.get('title', ''))}</a>
            <div class="meta">{html.escape(p.get('audience') or '')} · {asin} · <span class="{conf_class}">Merch POD confirmed</span></div>
          </td>
          <td>{fmt_num(p.get('category_position'))}</td>
          {deltas}
          <td><span class="score">{float(p.get('blended_score') or 0):.1f}</span></td>
          <td>{fmt_price(p.get('price'))}</td>
          <td>{fmt_num(p.get('reviews'))}</td>
          <td class="actions"><button onclick="showBrief('{asin}')">Brief</button><button class="reject" onclick="rejectAsin('{asin}')">Reject</button></td>
        </tr>"""


def table(rows: list[str], headers: list[str], empty_msg: str) -> str:
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join(rows) if rows else f'<tr><td colspan="{len(headers)}">{empty_msg}</td></tr>'
    return (f'<div class="tablebox"><table><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")


def build_dashboard(latest: dict) -> None:
    SITE.mkdir(parents=True, exist_ok=True)
    movers = latest.get("products", []) or []
    entrants = latest.get("new_entrants", []) or []
    baseline_top = latest.get("baseline_top", []) or []
    meta = latest.get("snapshot_meta", {}) or {}

    mover_headers = ["#", "", "Product", "Category rank", "24h", "7d", "Score", "Price", "Reviews", ""]
    entrant_headers = ["#", "", "Product", "Category rank", "Rank score", "Price", "Reviews", ""]

    mover_rows = [product_row(i, p, True) for i, p in enumerate(movers, 1)]
    entrant_rows = [product_row(i, p, False) for i, p in enumerate(entrants, 1)]
    baseline_rows = [product_row(i, p, False) for i, p in enumerate(baseline_top, 1)]

    baseline_section = ""
    if baseline_rows:
        baseline_section = (
            "<h2>Today's leaders (first run)</h2>"
            '<div class="h2note">No prior snapshot exists yet, so no momentum can be measured. '
            "These are simply the current top sellers. This section disappears once a second "
            "collect run gives the scorer something to compare against.</div>"
            + table(baseline_rows, entrant_headers, "")
        )

    generated = latest.get("generated_at_pacific") or latest.get("generated_at") or "Not yet run"
    note = "Quick proof run" if latest.get("mode") == "quick" else "Full daily run"

    b24 = meta.get("baseline_24h") or "none yet"
    b7 = meta.get("baseline_7d") or "none yet"
    baseline_line = (f"Snapshot {html.escape(str(meta.get('snapshot_date', '—')))} · "
                     f"compared against {html.escape(str(b24))} (24h) and {html.escape(str(b7))} (7d) · "
                     f"{fmt_num(meta.get('population'))} ASINs tracked" +
                     (f" · {meta['ip_filtered']} listing(s) filtered "
                       f"({', '.join(f'{v} {k}' for k, v in sorted((meta.get('filtered') or {}).items()))})"
                      if meta.get("ip_filtered") else ""))

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Amazon Shirt Scout</title>
<style>
:root{{--bg:#0c1117;--panel:#121922;--line:#263241;--text:#edf3f8;--muted:#91a1b3;--good:#55d18a;--bad:#ff7777;--accent:#73b7ff;--warn:#ffc46b}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 Inter,system-ui,-apple-system,Segoe UI,sans-serif}}
.wrap{{max-width:1500px;margin:auto;padding:28px}} h1{{font-size:28px;margin:0 0 4px}} .sub{{color:var(--muted);margin-bottom:6px}} .baseline{{color:var(--muted);font-size:12px;margin-bottom:22px}}
h2{{font-size:15px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:30px 0 4px}} .h2note{{color:var(--muted);font-size:12px;margin-bottom:12px}}
.cards{{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:12px;margin:0 0 18px}} .card{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}} .card b{{font-size:20px;display:block}} .card span{{color:var(--muted);font-size:12px}}
.tablebox{{overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:12px}} table{{width:100%;border-collapse:collapse;min-width:1100px}} th,td{{padding:11px 10px;border-bottom:1px solid var(--line);vertical-align:middle}} th{{text-align:left;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em;position:sticky;top:0;background:#111821}} tr:last-child td{{border-bottom:0}} a{{color:var(--text);text-decoration:none}} a:hover{{color:var(--accent)}}
.rank{{font-size:18px;font-weight:700;width:44px}} .thumb{{width:70px}} .thumb img,.ph{{width:52px;height:62px;object-fit:contain;background:white;border-radius:6px}} .ph{{display:grid;place-items:center;color:#111;font-weight:700;font-size:10px}} .product{{min-width:330px;max-width:520px}} .meta{{color:var(--muted);font-size:11px;margin-top:4px}} .score{{font-weight:800;font-size:16px}} .actions{{white-space:nowrap}}
button{{border:1px solid var(--line);background:#182332;color:var(--text);border-radius:7px;padding:7px 9px;margin-right:5px;cursor:pointer;font:inherit}} button:hover{{border-color:#4a6078}} button:focus-visible{{outline:2px solid var(--accent);outline-offset:2px}} .reject{{color:#ffaaaa}}
.delta.pos{{color:var(--good);font-weight:700}} .delta.neg{{color:var(--bad);font-weight:700}}
.ipflag{{color:#ff9a9a;font-weight:700;margin-left:8px;border:1px solid #5c2a2a;background:#2a1414;border-radius:4px;padding:1px 5px}}
.conf{{font-weight:600}} .conf-high{{color:var(--good)}} .conf-medium{{color:var(--warn)}} .conf-low{{color:var(--muted)}}
.notice{{margin-top:14px;color:var(--muted);font-size:12px}}
dialog{{width:min(720px,92vw);background:#121922;color:var(--text);border:1px solid var(--line);border-radius:12px;padding:0}} dialog::backdrop{{background:#000a}} .modal{{padding:20px}} .modal h2{{margin-top:0;font-size:18px;text-transform:none;letter-spacing:0;color:var(--text)}}
.briefgrid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}} .briefbox{{border:1px solid var(--line);border-radius:8px;padding:12px}} .briefbox h3{{margin:0 0 6px;font-size:13px}} .briefbox p{{margin:0;color:#cad5df}} .briefbox.span2{{grid-column:1/-1}} .tm{{border-color:#4a3a1c;background:#1a1610}}
.modal .footer{{margin-top:18px;display:flex;justify-content:flex-end;gap:8px;align-items:center}} input[type=number]{{width:70px;background:#0d141c;border:1px solid var(--line);color:white;padding:7px;border-radius:7px}}
@media(max-width:800px){{.cards{{grid-template-columns:1fr 1fr}}.wrap{{padding:16px}}.briefgrid{{grid-template-columns:1fr}}}}
@media(prefers-reduced-motion:reduce){{*{{transition:none!important;animation:none!important}}}}
</style>
</head>
<body><div class="wrap">
<h1>Amazon Shirt Scout</h1>
<div class="sub">{html.escape(note)} · updated {html.escape(str(generated))}</div>
<div class="baseline">{baseline_line}</div>
<div class="cards">
  <div class="card"><b>{len(movers)}</b><span>Movers with history</span></div>
  <div class="card"><b>{len(entrants)}</b><span>New arrivals</span></div>
  <div class="card"><b>{fmt_num(meta.get('population'))}</b><span>ASINs tracked</span></div>
  <div class="card"><b>Boilerplate</b><span>Merch detection signal</span></div>
</div>

{baseline_section}

<h2>Climbing</h2>
<div class="h2note">Present in both snapshots, so the movement is real. Positive means the shirt moved up its category list.</div>
{table(mover_rows, mover_headers, "No movers yet. Two collect runs are needed before momentum can be measured.")}

<h2>New arrivals</h2>
<div class="h2note">Absent from the previous snapshot. Ranked by current position only — no momentum is inferred, because most list churn at the bottom of the top 500 is noise.</div>
{table(entrant_rows, entrant_headers, "No new arrivals in this snapshot.")}

<div class="notice">Reject is stored in this browser for the POC and hides that ASIN on future refreshes. Check the trademark phrase in each brief before spending design time — infringement, not profanity, is what gets Merch accounts suspended.</div>
</div>

<dialog id="briefDialog"><div class="modal"><h2 id="briefTitle">Creative brief</h2><div id="briefContent"></div><div class="footer"><label>Variations <input id="variationCount" type="number" min="1" max="5" value="3"></label><button onclick="demoGenerate()">Generate options</button><button onclick="document.getElementById('briefDialog').close()">Close</button></div></div></dialog>

<script>
const DATA = {embed_json(latest)};
const ALL = [...(DATA.products || []), ...(DATA.new_entrants || []), ...(DATA.baseline_top || [])];
const byAsin = Object.fromEntries(ALL.map(x => [x.asin, x]));

function rejectedSet() {{
  try {{ return new Set(JSON.parse(localStorage.getItem('shirtScoutRejected') || '[]')); }}
  catch (e) {{ return new Set(); }}
}}
function applyRejected() {{
  const r = rejectedSet();
  document.querySelectorAll('tr[data-asin]').forEach(tr => {{
    if (r.has(tr.dataset.asin)) tr.style.display = 'none';
  }});
}}
function rejectAsin(asin) {{
  const r = rejectedSet();
  r.add(asin);
  localStorage.setItem('shirtScoutRejected', JSON.stringify([...r]));
  applyRejected();
}}
function fmtPct(v) {{
  if (v === null || v === undefined) return '—';
  return (v > 0 ? '+' : '') + Number(v).toFixed(1) + '%';
}}
function esc(s) {{
  return String(s === null || s === undefined ? '' : s)
    .replace(/[&<>'"]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}}[c]));
}}
function showBrief(asin) {{
  const p = byAsin[asin];
  if (!p) return;
  const phrase = p.slogan || p.title || '';
  const why = p.is_new_entrant
    ? 'New to the list at position ' + p.category_position
    : '24h ' + fmtPct(p.change_24h_pct) + ' · 7d ' + fmtPct(p.change_7d_pct) + ' · score ' + p.blended_score;

  document.getElementById('briefTitle').textContent = 'Creative brief: ' + phrase;
  document.getElementById('briefContent').innerHTML =
    '<div class="briefgrid">' +
      '<div class="briefbox"><h3>Niche signal</h3><p>' + esc(phrase) + '</p></div>' +
      '<div class="briefbox"><h3>Why it surfaced</h3><p>' + esc(why) + '</p></div>' +
      '<div class="briefbox span2 tm"><h3>Check this before designing</h3><p>Search <b>' + esc(phrase) +
        '</b> at <a href="https://tmsearch.uspto.gov/" target="_blank" rel="noopener">tmsearch.uspto.gov</a> ' +
        'for a live registration in class 025 (clothing). A trending phrase is exactly the kind most likely to be claimed. ' +
        '<button onclick="navigator.clipboard.writeText(' + JSON.stringify(phrase).replace(/"/g, '&quot;') + ')">Copy phrase</button></p></div>' +
      '<div class="briefbox"><h3>Concept 1</h3><p>Typography-led. Bold enough to read at thumbnail size, 3–4 colors, clear hierarchy.</p></div>' +
      '<div class="briefbox"><h3>Concept 2</h3><p>Original emblem or badge built from niche-specific symbols. Do not copy the source layout.</p></div>' +
      '<div class="briefbox"><h3>Concept 3</h3><p>Flat vector illustration with a different hook and composition from the source.</p></div>' +
      '<div class="briefbox"><h3>Print rules</h3><p>Flat vector, 3–5 harmonious colors, clean edges, exact typography, no distressing.</p></div>' +
    '</div>';
  document.getElementById('briefDialog').showModal();
}}
function demoGenerate() {{
  alert('No image generation provider is connected yet. The variation control is wired up and waiting for a backend.');
}}
document.querySelectorAll('.delta').forEach(el => {{
  const v = parseFloat(el.dataset.value);
  if (!Number.isNaN(v)) el.classList.add(v >= 0 ? 'pos' : 'neg');
}});
applyRejected();
</script>
</body></html>"""
    (SITE / "index.html").write_text(page, encoding="utf-8")


if __name__ == "__main__":
    latest = json.loads((ROOT / "data" / "latest.json").read_text(encoding="utf-8"))
    build_dashboard(latest)
