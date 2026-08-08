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

    clusters = latest.get("niche_clusters", []) or []
    clusters_section = ""
    if clusters:
        cards = "".join(
            '<div class="clcard">'
            f'<div class="clsize">{c["size"]} shirts</div>'
            f'<div class="clniche">{html.escape(c["niche"])}</div>'
            f'<div class="clmeta">avg score {c["avg_score"]} · best 24h '
            f'{("+" if c["best_change_24h"] > 0 else "")}{c["best_change_24h"]:.0f}%</div>'
            '</div>'
            for c in clusters[:8]
        )
        clusters_section = (
            '<h2>Rising niches</h2>'
            '<div class="h2note">Multiple shirts climbing under one theme in the same run. '
            'A cluster is a stronger signal than any single mover — it points at demand for '
            'the topic, not one lucky listing.</div>'
            f'<div class="clusters">{cards}</div>'
        )

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
.clusters{{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px;margin-bottom:8px}}
.clcard{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}}
.clsize{{font-size:12px;color:var(--accent);font-weight:700;text-transform:uppercase;letter-spacing:.04em}}
.clniche{{font-size:17px;font-weight:700;margin:4px 0 6px;text-transform:capitalize}}
.clmeta{{color:var(--muted);font-size:12px}}
.settings{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-bottom:18px}}
.settings summary{{cursor:pointer;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.05em}}
.setrow{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:10px}}
.setrow label{{font-size:12px;color:var(--muted)}}
.setrow input,.setrow select{{background:#0d141c;border:1px solid var(--line);color:var(--text);padding:7px;border-radius:7px;font:inherit;margin-left:6px}}
.setrow input#apiKey{{min-width:290px}}
.sethint{{color:var(--muted);font-size:11px;margin-top:9px}}
.genrow{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px}}
.genrow input{{width:64px;background:#0d141c;border:1px solid var(--line);color:var(--text);padding:7px;border-radius:7px;margin-left:6px}}
button.primary{{background:#1d3a5c;border-color:#2f5c8a}}
.genstatus{{color:var(--muted);font-size:12px}}
.genresults{{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px}}
.gencell{{border:1px solid var(--line);border-radius:8px;padding:9px;background:#0d141c}}
.gencell img{{width:100%;border-radius:6px;background:#fff;display:block;background-image:linear-gradient(45deg,#ddd 25%,transparent 25%),linear-gradient(-45deg,#ddd 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#ddd 75%),linear-gradient(-45deg,transparent 75%,#ddd 75%);background-size:14px 14px;background-position:0 0,0 7px,7px -7px,-7px 0}}
.genlabel{{font-size:11px;color:var(--muted);margin-bottom:7px}}
.genwait{{color:var(--muted);font-size:12px;padding:22px 0;text-align:center}}
.generr{{color:var(--bad);font-size:11px;word-break:break-word}}
.dl{{display:inline-block;margin-top:7px;font-size:12px;color:var(--accent)}}
.kwbox .chiprow{{margin:6px 0;font-size:12px;color:var(--muted)}}
.kwbox .chiprow b{{display:inline-block;min-width:58px;color:var(--text)}}
.chip{{display:inline-block;background:#0d141c;border:1px solid var(--line);border-radius:14px;padding:3px 9px;margin:3px 4px 3px 0;font-size:12px;color:var(--text)}}
.chip i{{color:var(--muted);font-style:normal;margin-left:5px;font-size:11px}}
.kwnote{{color:var(--muted);font-size:11px;margin-top:8px}}
.kwbox .fld{{display:block;font-size:11px;color:var(--muted);margin-top:8px}}
.kwbox textarea{{width:100%;min-height:44px;background:#0d141c;border:1px solid var(--line);color:#cad5df;border-radius:7px;padding:8px;font:13px/1.4 ui-monospace,Menlo,monospace;margin-top:3px;resize:vertical}}
.promptbox textarea{{width:100%;min-height:74px;background:#0d141c;border:1px solid var(--line);color:#cad5df;border-radius:7px;padding:9px;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;resize:vertical;margin-bottom:8px}}
.conf{{font-weight:600}} .conf-high{{color:var(--good)}} .conf-medium{{color:var(--warn)}} .conf-low{{color:var(--muted)}}
.hint{{color:var(--muted);font-size:12px;margin-right:auto}}
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
<details class="settings"><summary>Settings — image generation</summary>
  <div class="setrow">
    <label>API key <input id="apiKey" type="password" placeholder="sk-..." autocomplete="off"></label>
    <button onclick="saveKey()">Save</button>
    <span id="keyStatus" class="genstatus"></span>
  </div>
  <div class="setrow">
    <label>Model <select id="genModel"><option value="gpt-image-1.5">gpt-image-1.5</option><option value="gpt-image-2">gpt-image-2</option><option value="gpt-image-1">gpt-image-1</option><option value="gpt-image-1-mini">gpt-image-1-mini (cheap)</option><option value="dall-e-3">dall-e-3</option></select></label>
    <label>Background <select id="genBg"><option value="transparent">transparent</option><option value="opaque">opaque</option><option value="auto">auto</option></select></label>
    <label>Quality <select id="genQuality"><option value="medium">medium</option><option value="high">high</option><option value="low">low (cheap)</option></select></label>
    <label>Size <select id="genSize"><option value="1024x1024">1024x1024</option><option value="1024x1536">1024x1536 (portrait)</option></select></label>
  </div>
  <div class="sethint">The key is stored only in this browser and is never committed to the repo. Each variation is a separate billed image request. GPT Image models may require API Organization Verification in your OpenAI console before they will run.</div>
</details>
<div class="cards">
  <div class="card"><b>{len(movers)}</b><span>Movers with history</span></div>
  <div class="card"><b>{len(entrants)}</b><span>New arrivals</span></div>
  <div class="card"><b>{fmt_num(meta.get('population'))}</b><span>ASINs tracked</span></div>
  <div class="card"><b>Boilerplate</b><span>Merch detection signal</span></div>
</div>

{baseline_section}
{clusters_section}

<h2>Climbing</h2>
<div class="h2note">Present in both snapshots, so the movement is real. Positive means the shirt moved up its category list.</div>
{table(mover_rows, mover_headers, "No movers yet. Two collect runs are needed before momentum can be measured.")}

<h2>New arrivals</h2>
<div class="h2note">Absent from the previous snapshot. Ranked by current position only — no momentum is inferred, because most list churn at the bottom of the top 500 is noise.</div>
{table(entrant_rows, entrant_headers, "No new arrivals in this snapshot.")}

<div class="notice">Reject is stored in this browser for the POC and hides that ASIN on future refreshes. Check the trademark phrase in each brief before spending design time — infringement, not profanity, is what gets Merch accounts suspended.</div>
</div>

<dialog id="briefDialog"><div class="modal"><h2 id="briefTitle">Creative brief</h2><div id="briefContent"></div><div class="footer"><span class="hint">Paste into ChatGPT or your image model, then set the type yourself.</span><button onclick="document.getElementById('briefDialog').close()">Close</button></div></div></dialog>

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
function copyText(btn, text) {{
  navigator.clipboard.writeText(text).then(() => {{
    const was = btn.textContent;
    btn.textContent = 'Copied';
    setTimeout(() => {{ btn.textContent = was; }}, 1200);
  }});
}}
function listingPanel(p) {{
  const kw = p.keyword_report, L = p.listing;
  if (!kw && !L) return '';
  const chips = (arr) => (arr || []).map(x =>
    '<span class="chip">' + esc(x.term) + '<i>' + x.count + '</i></span>').join('');
  let html = '<div class="briefbox span2 kwbox"><h3>Niche keywords</h3>';
  if (kw && kw.phrases && kw.phrases.length)
    html += '<div class="chiprow"><b>Phrases</b> ' + chips(kw.phrases) + '</div>';
  if (kw && kw.words && kw.words.length)
    html += '<div class="chiprow"><b>Words</b> ' + chips(kw.words) + '</div>';
  if (kw) html += '<div class="kwnote">From ' + kw.sample_size + ' shirt titles in this niche.</div>';
  if (L) {{
    html += '<h3 style="margin-top:14px">Draft listing</h3>' +
      '<label class="fld">Title<textarea readonly id="ltitle">' + esc(L.title) + '</textarea></label>' +
      '<button onclick="copyText(this, document.getElementById(\'ltitle\').value)">Copy title</button>';
    (L.bullets || []).forEach((b, i) => {{
      html += '<label class="fld">Bullet ' + (i+1) + '<textarea readonly id="lb' + i + '">' + esc(b) + '</textarea></label>' +
        '<button onclick="copyText(this, document.getElementById(\'lb' + i + '\').value)">Copy</button>';
    }});
    html += '<div class="kwnote">Edit before use — this is scaffolding, not a finished listing. Verify the phrase has no live trademark in class 025.</div>';
  }}
  return html + '</div>';
}}
function genPanel(p) {{
  const max = (p.design_prompts || []).length || 3;
  return '<div class="briefbox span2 genbox">' +
      '<h3>Generate designs</h3>' +
      '<div class="genrow">' +
        '<label>Variations <input id="varCount" type="number" min="1" max="' + max + '" value="3"></label>' +
        '<button id="genBtn" class="primary" onclick="generateDesigns()">Generate</button>' +
        '<span id="genStatus" class="genstatus"></span>' +
      '</div>' +
      '<div id="genResults" class="genresults"></div>' +
    '</div>';
}}

// ---- image generation ---------------------------------------------------
// The key lives only in this browser's localStorage. It is never written to
// the repo, so it stays out of the public site.
function getKey() {{ try {{ return localStorage.getItem('shirtScoutApiKey') || ''; }} catch (e) {{ return ''; }} }}
function saveKey() {{
  const v = document.getElementById('apiKey').value.trim();
  localStorage.setItem('shirtScoutApiKey', v);
  document.getElementById('keyStatus').textContent = v ? 'Key saved in this browser' : 'Key cleared';
}}

async function generateOne(prompt, key, model, size) {{
  const res = await fetch('https://api.openai.com/v1/images/generations', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key }},
    body: JSON.stringify(Object.assign(
      {{ model: model, prompt: prompt, n: 1, size: size }},
      // Transparency and format are GPT-Image-only parameters; sending them to
      // dall-e-3 makes the request fail.
      model.indexOf('gpt-image') === 0
        ? {{ background: document.getElementById('genBg').value,
             output_format: 'png',
             quality: document.getElementById('genQuality').value }}
        : {{}}
    ))
  }});
  if (!res.ok) {{
    let msg = res.status + ' ' + res.statusText;
    try {{ const e = await res.json(); if (e.error && e.error.message) msg = e.error.message; }} catch (e) {{}}
    throw new Error(msg);
  }}
  const data = await res.json();
  const d = (data.data || [])[0] || {{}};
  return d.b64_json ? 'data:image/png;base64,' + d.b64_json : d.url;
}}

async function generateDesigns() {{
  const key = getKey();
  const status = document.getElementById('genStatus');
  const results = document.getElementById('genResults');
  const btn = document.getElementById('genBtn');
  if (!key) {{
    status.textContent = 'Add an API key in Settings at the top of the page first.';
    return;
  }}
  const p = byAsin[CURRENT_ASIN];
  const dirs = (p && p.design_prompts) || [];
  const n = Math.max(1, Math.min(parseInt(document.getElementById('varCount').value, 10) || 3, dirs.length));
  const model = document.getElementById('genModel').value;
  const size = document.getElementById('genSize').value;

  btn.disabled = true;
  results.innerHTML = '';
  let done = 0;

  // One call per direction, so every variation is a different concept.
  const tasks = dirs.slice(0, n).map(async (d) => {{
    const cell = document.createElement('div');
    cell.className = 'gencell';
    cell.innerHTML = '<div class="genlabel">' + esc(d.label) + '</div><div class="genwait">working…</div>';
    results.appendChild(cell);
    try {{
      const src = await generateOne(d.prompt, key, model, size);
      cell.innerHTML = '<div class="genlabel">' + esc(d.label) + '</div>' +
        '<img src="' + src + '" alt="">' +
        '<a class="dl" download="' + esc(d.label.replace(/[^a-z0-9]+/ig, '-').toLowerCase()) + '.png" href="' + src + '">Download</a>';
    }} catch (err) {{
      cell.innerHTML = '<div class="genlabel">' + esc(d.label) + '</div>' +
        '<div class="generr">' + esc(err.message) + '</div>';
    }}
    done++;
    status.textContent = done + ' of ' + n + ' finished';
  }});

  status.textContent = 'Generating ' + n + '…';
  await Promise.all(tasks);
  btn.disabled = false;
}}
let CURRENT_ASIN = null;
function showBrief(asin) {{
  const p = byAsin[asin];
  if (!p) return;
  CURRENT_ASIN = asin;
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
      listingPanel(p) +
      genPanel(p) +
    '</div>';
  document.getElementById('briefDialog').showModal();
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
