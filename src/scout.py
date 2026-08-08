"""Amazon novelty T-shirt Best Sellers tracker built on Keepa.

Two stages, run as separate jobs so a token wait never stalls a single run:

  collect  -- store today's ranked ASIN list per audience. No product queries.
  enrich   -- score momentum from stored snapshots, then query metadata for
              only the top candidates and rebuild the dashboard.

Momentum is derived from our own daily snapshots, so no per-product Keepa
history query is ever needed.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import keepa

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SNAPSHOT_DIR = DATA / "snapshots"
CONFIG_PATH = ROOT / "config.json"
PACIFIC = ZoneInfo("America/Los_Angeles")

# Word-boundary aware. Matches "t-shirt", "t shirt", "tshirt", "tee", "tees".
# Deliberately does NOT substring-match "tee", which would catch canteen,
# committee, sixteen, volunteer.
TSHIRT_RE = re.compile(r"\bt-?\s?shirts?\b|\btees?\b", re.IGNORECASE)

# Merch by Amazon injects identical boilerplate into every listing's feature
# bullets. This is a far stronger signal than "Amazon is the seller".
MERCH_MARKERS = (
    "solid colors: 100% cotton",
    "heather grey: 90% cotton",
    "lightweight, classic fit",
    "double-needle sleeve and bottom hem",
)

CONFIDENCE_ORDER = {"Low": 0, "Medium": 1, "High": 2}

LICENSED_MARKERS = (
    "officially licensed",
    "official licensed",
    "official product",
    "licensed by",
    "under license",
    "all rights reserved",
    "trademarks of",
    "trademark of",
    "™",
    "©",
)


def licensed_signals(product: dict[str, Any]) -> list[str]:
    """Explicit licensing language anywhere in the bullets or description."""
    blob = " ".join(str(f) for f in (product.get("features") or [])).lower()
    blob += " " + str(product.get("description") or "").lower()
    return [m for m in LICENSED_MARKERS if m in blob]


DEFAULT_IP_WATCHLIST = [
    "marvel", "spider-man", "spiderman", "disney", "pixar", "star wars", "dc comics",
    "batman", "superman", "harry potter", "pokemon", "nintendo", "hello kitty",
    "nfl", "nba", "mlb", "nhl", "nasa", "coca-cola", "jeep", "peanuts", "snoopy",
    "champion", "hanes", "fruit of the loom", "nike", "adidas", "under armour",
]


def ip_risk_terms(*fields: str | None, watchlist: list[str]) -> list[str]:
    """Watchlist terms appearing in any supplied field (title, brand, etc.)."""
    text = " " + " ".join((f or "").lower() for f in fields) + " "
    return [t for t in watchlist if re.search(rf"\b{re.escape(t.lower())}\b", text)]


# --------------------------------------------------------------------------
# IO helpers
# --------------------------------------------------------------------------

def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def snapshot_path(day: date) -> Path:
    return SNAPSHOT_DIR / f"{day.isoformat()}.json"


def available_snapshot_dates() -> list[date]:
    """Sorted list of dates we have a snapshot file for."""
    if not SNAPSHOT_DIR.exists():
        return []
    out = []
    for p in SNAPSHOT_DIR.glob("*.json"):
        try:
            out.append(date.fromisoformat(p.stem))
        except ValueError:
            continue
    return sorted(out)


def load_snapshot(day: date) -> dict[str, dict[str, int]]:
    return load_json(snapshot_path(day), {})


def prune_snapshots(retention_days: int) -> None:
    dates = available_snapshot_dates()
    for d in dates[:-retention_days] if retention_days > 0 else []:
        snapshot_path(d).unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Math helpers
# --------------------------------------------------------------------------

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def pct_improvement(old: float | None, current: float | None) -> float | None:
    """Positive means the item moved UP the list (toward position 1)."""
    if not old or not current:
        return None
    return ((float(old) - float(current)) / float(old)) * 100.0


def rank_quality(position: int, depth: int) -> float:
    """0-100, log-scaled: position 1 is 100, position `depth` is ~0."""
    q = 100.0 * (1.0 - math.log10(max(position, 1)) / math.log10(max(depth + 1, 2)))
    return clamp(q, 0.0, 100.0)


def find_baseline(
    dates: list[date], target: date, exclude: date, tolerance: int
) -> date | None:
    """Closest snapshot to `target`, never `exclude`, within `tolerance` days.

    Excluding the current day matters: without it, a failed overnight collect
    means today's own snapshot becomes the "24h ago" baseline and every ASIN
    silently reports 0% change.
    """
    cands = [
        (abs((d - target).days), -d.toordinal(), d)
        for d in dates
        if d != exclude and abs((d - target).days) <= tolerance
    ]
    if not cands:
        return None
    cands.sort()
    return cands[0][2]


def percentile_scores(
    rows: list[dict[str, Any]], key: str, higher_is_better: bool = True
) -> dict[str, float | None]:
    """Percentile rank per ASIN, or None where the value is missing.

    Missing values return None rather than a neutral 50 so that callers can
    renormalize weights instead of quietly promoting unknowns above decliners.
    """
    vals = [(r["asin"], r.get(key)) for r in rows if r.get(key) is not None]
    if not vals:
        return {r["asin"]: None for r in rows}
    vals.sort(key=lambda x: x[1], reverse=higher_is_better)

    # Ties share a score, so identical values can't be split by sort order.
    scores: dict[str, float] = {}
    n = max(len(vals) - 1, 1)
    i = 0
    while i < len(vals):
        j = i
        while j + 1 < len(vals) and vals[j + 1][1] == vals[i][1]:
            j += 1
        shared = 100.0 * (1.0 - ((i + j) / 2.0) / n)
        for k in range(i, j + 1):
            scores[vals[k][0]] = round(shared, 4)
        i = j + 1
    return {r["asin"]: scores.get(r["asin"]) for r in rows}


# --------------------------------------------------------------------------
# Keepa product helpers
# --------------------------------------------------------------------------

def current_stat(product: dict[str, Any], key: str):
    return (product.get("stats_parsed") or {}).get("current", {}).get(key)


def normalize_money(value) -> float | None:
    """stats_parsed already divides prices by 100 and drops negatives.

    keepa/utils.py::_normalize_value -> `v = float(v) / 100` for price fields,
    and returns None for v < 0 so the key is absent entirely. Dividing again
    here is what turned $19.99 into $0.20.
    """
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def has_amazon_offer(product: dict[str, Any]) -> bool:
    """Amazon is currently selling this ASIN directly.

    The AMAZON key is simply absent when there's no live Amazon offer, so a
    presence check is the whole test. No -1 sentinel ever reaches us.
    """
    return current_stat(product, "AMAZON") is not None


def review_count(product: dict[str, Any]) -> int | None:
    value = current_stat(product, "COUNT_REVIEWS")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def likely_tshirt(title: str | None) -> bool:
    return bool(title) and bool(TSHIRT_RE.search(title))


def merch_confidence(product: dict[str, Any]) -> tuple[str, int]:
    """Score Merch by Amazon likelihood off the boilerplate feature bullets."""
    features = product.get("features") or []
    blob = " ".join(str(f) for f in features).lower()
    hits = sum(1 for m in MERCH_MARKERS if m in blob)
    if hits >= 2:
        return "High", hits
    if hits == 1:
        return "Medium", hits
    if has_amazon_offer(product) and likely_tshirt(product.get("title")):
        return "Medium", hits
    return "Low", hits


def image_url(product: dict[str, Any]) -> str | None:
    raw = product.get("imagesCSV") or ""
    first = raw.split(",")[0].strip() if raw else ""
    return f"https://m.media-amazon.com/images/I/{first}" if first else None


def slogan_from_title(title: str) -> str:
    """Strip garment noise so the remainder can be trademark-checked."""
    text = TSHIRT_RE.sub(" ", title or "")
    text = re.sub(
        r"\b(mens?|womens?|girls?|boys?|kids?|youth|unisex|adult|funny|vintage|"
        r"retro|graphic|gift|gifts|for|premium|classic|cotton|short sleeve|"
        r"long sleeve|tank top|hoodie|sweatshirt|pullover)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"[^\w\s'&-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -&")
    # Drop dangling possessives and stray single letters left behind by removals.
    text = re.sub(r"\s+'s\b", "", text)
    text = re.sub(r"\b\d+\s*$", "", text).strip(" -&")
    return text[:80] or (title or "")[:80]


# Image models render text badly, so every prompt forbids it outright — type
# gets set by hand afterward in a vector tool.
NEGATIVE = ("no text, no words, no letters, no numbers, no typography, no watermark, "
            "no logos, no brand marks, no photorealism, no gradients, no drop shadows, "
            "no busy background detail")


def design_prompts(niche: str) -> list[dict[str, str]]:
    """Six genuinely distinct illustration directions for one niche.

    Each variation the user requests pulls the next direction, so asking for
    three gives three different concepts rather than three renders of one.
    """
    n = (niche or "").strip() or "the theme"
    base = f"plain white background, print-ready t-shirt graphic. {NEGATIVE}."
    return [
        {"label": "Emblem / badge",
         "prompt": f"Flat vector emblem illustration about {n}. Circular badge composition, "
                   f"bold simple shapes, thick clean outlines, symmetrical and centered, "
                   f"limited palette of 3 flat colors, high contrast, {base}"},
        {"label": "Single subject",
         "prompt": f"Flat vector illustration of a single centered subject representing {n}. "
                   f"Sticker style, bold simple shapes, thick clean outlines, limited palette "
                   f"of 4 flat colors, no shading, {base}"},
        {"label": "Retro scene",
         "prompt": f"Retro 1970s flat vector illustration about {n}. Horizontal sunset stripe "
                   f"arc behind a simple bold subject, muted vintage palette of burnt orange, "
                   f"mustard, cream and teal, clean edges, centered, {base}"},
        {"label": "Monoline line art",
         "prompt": f"Minimal monoline line-art illustration about {n}. Single consistent stroke "
                   f"weight, one color on white, continuous clean linework, elegant and simple, "
                   f"centered composition, generous negative space, {base}"},
        {"label": "Cartoon mascot",
         "prompt": f"Playful flat cartoon mascot character representing {n}. Friendly expressive "
                   f"face, chunky rounded shapes, bold outlines, bright limited palette of 5 flat "
                   f"colors, centered full-body character, sticker style, {base}"},
        {"label": "Geometric minimal",
         "prompt": f"Minimal geometric flat illustration about {n}. Built from simple circles, "
                   f"triangles and rectangles, bauhaus-inspired, 3 flat colors, strong symmetry, "
                   f"lots of negative space, centered, {base}"},
    ]


STOPWORDS = {
    "the","a","an","and","or","for","with","your","you","this","that","tee","tees",
    "shirt","shirts","tshirt","tshirts","t-shirt","t-shirts","mens","men","womens",
    "women","kids","boys","girls","youth","unisex","adult","gift","gifts","funny",
    "vintage","retro","graphic","premium","classic","cotton","short","long","sleeve",
    "novelty","cool","cute","best","top","of","to","in","on","by","is","it","my",
    "size","fit","crew","neck","design","print","printed","apparel","clothing",
}


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if w]


def keyword_report(titles: list[str], max_terms: int = 12) -> dict[str, Any]:
    """Rank phrases/words by how many DISTINCT titles use them.

    Document frequency, not raw count: a bigram appearing once each in five
    different shirts is a real search term; one appearing five times inside a
    single repeated title is noise. Bigrams are only kept when at least two
    different shirts share them.
    """
    from collections import Counter
    uniq = list(dict.fromkeys(t for t in titles if t))
    uni_df: Counter = Counter()
    bi_df: Counter = Counter()
    for t in uniq:
        toks = _words(t)
        ws = {w for w in toks if w not in STOPWORDS and len(w) > 2}
        uni_df.update(ws)
        seen_bi = set()
        for a, b in zip(toks, toks[1:]):
            if (a not in STOPWORDS and b not in STOPWORDS
                    and len(a) > 2 and len(b) > 2):
                seen_bi.add(f"{a} {b}")
        bi_df.update(seen_bi)

    n = max(len(uniq), 1)
    phrases = [{"term": k, "count": v} for k, v in bi_df.most_common(max_terms)
               if v >= 2]
    words = [{"term": k, "count": v} for k, v in uni_df.most_common(max_terms)
             if v >= 2 or n < 3]
    return {"phrases": phrases, "words": words, "sample_size": n}


def cluster_niches(rows: list[dict[str, Any]], min_size: int = 2) -> list[dict[str, Any]]:
    """Group surfaced rows sharing a dominant keyword into niche clusters.

    A single mover is easy to explain away as a one-off spike. Two or more
    shirts climbing under the same theme in the same run is the signal worth
    designing for, because it reflects demand for the topic, not one listing.
    """
    from collections import defaultdict
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        rep = r.get("keyword_report") or {}
        phrases = [ph for ph in (rep.get("phrases") or []) if ph.get("count", 0) >= 2]
        if not phrases:
            continue  # no phrase shared across ≥2 shirts → not a cluster member
        key = phrases[0]["term"]
        buckets[key].append(r)

    clusters = []
    for key, members in buckets.items():
        if len(members) < min_size:
            continue
        members = sorted(members, key=lambda m: m.get("blended_score") or 0, reverse=True)
        clusters.append({
            "niche": key,
            "size": len(members),
            "avg_score": round(sum(m.get("blended_score") or 0 for m in members) / len(members), 1),
            "best_change_24h": max((m.get("change_24h_pct") or 0) for m in members),
            "asins": [m["asin"] for m in members],
            "titles": [m.get("title", "") for m in members][:6],
        })
    # Rank clusters by size first (breadth of the trend), then momentum.
    clusters.sort(key=lambda c: (c["size"], c["avg_score"]), reverse=True)
    return clusters


def suggested_listing(niche: str, kw: dict[str, Any]) -> dict[str, Any]:
    """A paste-ready title and two bullets, built from the mined phrases.

    Every Merch listing is the seller's own text, so this is scaffolding to
    edit, never a copy of any competitor's listing.
    """
    base = (niche or "Graphic").strip()
    base_words = set(_words(base))
    # Phrases carry more search intent than loose words; drop numeric/date noise
    # and anything already implied by the niche name.
    phrases = [p["term"] for p in kw.get("phrases", [])
               if not any(c.isdigit() for c in p["term"])
               and not set(_words(p["term"])) <= base_words][:3]
    phrase_words = {w for ph in phrases for w in _words(ph)}
    words = [w["term"] for w in kw.get("words", [])
             if not w["term"].isdigit() and w["term"] not in base_words
             and w["term"] not in phrase_words
             and w["term"] not in {"est", "since", "club", "squad", "ever"}][:4]

    title = f"{base.title()} T-Shirt"
    tail = ", ".join(p.title() for p in phrases)
    if tail:
        title = f"{base.title()} T-Shirt - {tail}"

    love = ", ".join(dict.fromkeys(phrases + words)[:3]) if False else \
           ", ".join(list(dict.fromkeys(phrases + words))[:3])
    return {
        "title": title[:140],
        "bullets": [
            f"Original {base.lower()} design" +
            (f" for fans of {love}." if love else "."),
            "Makes a great gift idea. Lightweight, classic fit, "
            "double-needle sleeve and bottom hem.",
        ],
        "keywords_used": list(dict.fromkeys(phrases + words)),
    }


def title_is_blocked(title: str, blocked_terms: list[str]) -> bool:
    text = f" {(title or '').lower()} "
    return any(re.search(rf"\b{re.escape(t.lower())}\b", text) for t in blocked_terms)


def cache_is_fresh(row: dict[str, Any], days: int) -> bool:
    stamp = (row or {}).get("fetched_at")
    if not stamp:
        return False
    try:
        fetched = datetime.fromisoformat(stamp)
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - fetched <= timedelta(days=days)
    except ValueError:
        return False


# --------------------------------------------------------------------------
# Collect stage
# --------------------------------------------------------------------------

def selected_categories(cfg: dict[str, Any], mode: str) -> dict[str, int]:
    categories = cfg["categories"]
    return {"Men": categories["Men"]} if mode == "quick" else dict(categories)


def collect_rankings(api: keepa.Keepa, cfg: dict[str, Any], mode: str) -> None:
    categories = selected_categories(cfg, mode)
    depth = int(cfg.get("snapshot_depth", 500))
    today = datetime.now(PACIFIC).date()

    snapshot = load_snapshot(today)
    collected, failed = [], []

    print(f"Collect stage mode={mode}; Keepa tokens at start={api.tokens_left}")
    for audience, category_id in categories.items():
        print(f"[{audience}] requesting Best Sellers for node {category_id} ...")
        try:
            asins = api.best_sellers_query(str(category_id), domain="US")
        except Exception as exc:
            print(f"[{audience}] query failed: {exc}")
            failed.append(audience)
            continue

        # Keepa returns None for a node with no Best Sellers data. Guard it so
        # one dead node can't discard audiences already collected in this run.
        if not asins:
            print(f"[{audience}] no data returned for node {category_id}; skipping")
            failed.append(audience)
            continue

        ranked = [a for a in asins[:depth] if a]
        snapshot[audience] = {asin: idx + 1 for idx, asin in enumerate(ranked)}
        collected.append(audience)
        print(f"[{audience}] saved {len(ranked)} ranked ASINs; tokens now={api.tokens_left}")

    if not collected:
        raise SystemExit("No audiences collected. Nothing written.")

    save_json(snapshot_path(today), snapshot)
    prune_snapshots(int(cfg.get("snapshot_retention_days", 45)))

    save_json(DATA / "collect_status.json", {
        "snapshot_date": today.isoformat(),
        "last_collect_at": datetime.now(timezone.utc).isoformat(),
        "last_collect_at_pacific": datetime.now(PACIFIC).isoformat(),
        "mode": mode,
        "audiences_collected": collected,
        "audiences_failed": failed,
    })
    print(f"Collect stage complete: {len(collected)} audience(s). No product query made.")


# --------------------------------------------------------------------------
# Candidate scoring
# --------------------------------------------------------------------------

@dataclass
class Candidate:
    asin: str
    audience: str
    category_position: int
    change_24h_pct: float | None = None
    change_7d_pct: float | None = None
    baseline_24h: str | None = None
    baseline_7d: str | None = None
    is_new_entrant: bool = False
    rank_pct: float = 0.0
    blended_score: float = 0.0


def build_candidates(
    cfg: dict[str, Any], mode: str
) -> tuple[list[Candidate], list[Candidate], list[Candidate], dict]:
    """Return (movers, new_entrants, baseline_only, meta), scored and sorted."""
    dates = available_snapshot_dates()
    if not dates:
        raise SystemExit("No rank snapshots found. Run the collect stage first.")

    today = dates[-1]
    current = load_snapshot(today)
    depth = int(cfg.get("snapshot_depth", 500))
    rejected = set(load_json(DATA / "rejected.json", []))
    allowed = set(selected_categories(cfg, mode).keys())

    base24_date = find_baseline(dates, today - timedelta(days=1), today, tolerance=1)
    base7_date = find_baseline(dates, today - timedelta(days=7), today, tolerance=2)
    base24 = load_snapshot(base24_date) if base24_date else {}
    base7 = load_snapshot(base7_date) if base7_date else {}

    best: dict[str, Candidate] = {}
    for audience, current_map in current.items():
        if audience not in allowed:
            continue
        prev24 = base24.get(audience) or {}
        prev7 = base7.get(audience) or {}

        for asin, pos in current_map.items():
            if asin in rejected:
                continue

            old24 = prev24.get(asin)
            old7 = prev7.get(asin)

            # An ASIN absent from yesterday's list is a NEW ENTRANT, not a
            # 60% gainer. Imputing rank depth+1 made every list-churn artifact
            # outrank every real mover.
            is_new = bool(prev24) and old24 is None

            cand = Candidate(
                asin=asin,
                audience=audience,
                category_position=pos,
                change_24h_pct=pct_improvement(old24, pos),
                change_7d_pct=pct_improvement(old7, pos),
                baseline_24h=base24_date.isoformat() if base24_date else None,
                baseline_7d=base7_date.isoformat() if base7_date else None,
                is_new_entrant=is_new,
            )
            prior = best.get(asin)
            if prior is None or cand.category_position < prior.category_position:
                best[asin] = cand

    population = list(best.values())
    movers = [c for c in population if c.change_24h_pct is not None or c.change_7d_pct is not None]
    new_entrants = [c for c in population if c.is_new_entrant]

    # First run: no prior snapshot exists, so nothing can qualify as a mover or
    # a new arrival and both tables would render empty for a full day. Fall back
    # to current leaders so there is something to look at immediately.
    baseline_only: list[Candidate] = []
    if base24_date is None and base7_date is None:
        ranked = sorted(population, key=lambda c: c.category_position)
        # The very top of a category rank list is owned by blank-apparel brands
        # with years of steady volume. Skip past them, then spread the sample
        # across the remaining depth instead of taking one contiguous block.
        skip_top = int(cfg.get("baseline_skip_top", 0))
        spread = [c for c in ranked if c.category_position > skip_top]
        want = int(cfg.get("new_entrant_pool", 25))
        if spread and want and len(spread) > want:
            step = len(spread) / want
            spread = [spread[int(i * step)] for i in range(want)]
        baseline_only = spread
        for c in baseline_only:
            c.blended_score = round(rank_quality(c.category_position, depth), 1)

    # Percentiles are computed across the FULL category population, not the
    # enriched subset, so a score of 82 means the same thing every day.
    score_movers(movers, depth)
    for c in new_entrants:
        c.rank_pct = rank_quality(c.category_position, depth)
        c.blended_score = round(c.rank_pct, 1)

    movers.sort(key=lambda c: c.blended_score, reverse=True)
    new_entrants.sort(key=lambda c: c.category_position)

    meta = {
        "snapshot_date": today.isoformat(),
        "baseline_24h": base24_date.isoformat() if base24_date else None,
        "baseline_7d": base7_date.isoformat() if base7_date else None,
        "population": len(population),
        "movers_with_history": len(movers),
        "new_entrants": len(new_entrants),
        "first_run": bool(baseline_only),
    }
    return movers, new_entrants, baseline_only, meta


def score_movers(movers: list[Candidate], depth: int) -> None:
    """Blend 24h / 7d / current-rank percentiles, renormalizing per row.

    A brand-new repo has no 7d baseline. Rather than defaulting that component
    to a neutral 50, the available components absorb its weight.
    """
    rows = [{"asin": c.asin, "c24": c.change_24h_pct, "c7": c.change_7d_pct,
             "pos": c.category_position} for c in movers]
    p24 = percentile_scores(rows, "c24", higher_is_better=True)
    p7 = percentile_scores(rows, "c7", higher_is_better=True)
    ppos = percentile_scores(rows, "pos", higher_is_better=False)

    weights = {"c24": 0.45, "c7": 0.35, "pos": 0.20}
    for c in movers:
        parts = [
            (weights["c24"], p24.get(c.asin)),
            (weights["c7"], p7.get(c.asin)),
            (weights["pos"], ppos.get(c.asin)),
        ]
        usable = [(w, v) for w, v in parts if v is not None]
        total_w = sum(w for w, _ in usable)
        c.rank_pct = ppos.get(c.asin) or rank_quality(c.category_position, depth)
        c.blended_score = round(sum(w * v for w, v in usable) / total_w, 1) if total_w else 0.0


# --------------------------------------------------------------------------
# Enrich stage
# --------------------------------------------------------------------------

def fetch_metadata(
    api: keepa.Keepa, asins: list[str], cache: dict[str, Any], refresh_days: int,
    want_reviews: bool = True, skip: set[str] | None = None
) -> dict[str, dict[str, Any]]:
    skip = skip or set()
    stale = [a for a in asins
             if a not in skip and not cache_is_fresh(cache.get(a, {}), refresh_days)]
    if skip:
        print(f"Skipping {len(skip)} ASIN(s) already known to be trademarked")
    print(f"Candidates={len(asins)}; metadata queries needed={len(stale)}")
    if not stale:
        return {}
    # rating=True is REQUIRED for COUNT_REVIEWS to be populated. It costs an
    # extra token per ASIN; set fetch_reviews=false in config to skip it.
    details = api.query(stale, domain="US", history=False, stats=1,
                        rating=want_reviews, progress_bar=True)
    return {p.get("asin"): p for p in (details or []) if p.get("asin")}


def build_row(asin: str, product: dict[str, Any] | None, cached: dict[str, Any],
              now_utc: datetime, ip_watchlist: list[str]) -> dict[str, Any] | None:
    if product:
        title = product.get("title")
        if not title:
            return None
        confidence, hits = merch_confidence(product)
        flags = ip_risk_terms(title, product.get("brand"), product.get("manufacturer"),
                              watchlist=ip_watchlist)
        licensed = licensed_signals(product)
        return {
            "asin": asin,
            "title": title,
            "slogan": slogan_from_title(title),
            "design_prompts": design_prompts(slogan_from_title(title)),
            "keyword_report": None,   # filled after all titles are known
            "listing": None,
            "brand": product.get("brand"),
            "manufacturer": product.get("manufacturer"),
            "image_url": image_url(product),
            "price": normalize_money(current_stat(product, "AMAZON")),
            "reviews": review_count(product),
            "amazon_direct_offer": has_amazon_offer(product),
            "merch_confidence": confidence,
            "merch_marker_hits": hits,
            "ip_flags": flags,
            "licensed_markers": licensed,
            "fetched_at": now_utc.isoformat(),
        }
    # No fresh fetch: fall back to cache, but never emit a titleless row.
    if not cached.get("title"):
        return None
    return dict(cached)


def enrich_dashboard(api: keepa.Keepa, cfg: dict[str, Any], mode: str) -> None:
    movers, new_entrants, baseline_only, meta = build_candidates(cfg, mode)

    cache = load_json(DATA / "products.json", {})
    rejected = set(load_json(DATA / "rejected.json", []))
    blocked_terms = cfg.get("blocked_terms", [])
    require_amazon = bool(cfg.get("require_amazon_offer", True))
    min_conf = CONFIDENCE_ORDER.get(cfg.get("min_merch_confidence", "Medium"), 1)
    refresh_days = int(cfg.get("metadata_refresh_days", 7))
    ip_watchlist = cfg.get("ip_watchlist") or DEFAULT_IP_WATCHLIST
    block_ip = bool(cfg.get("block_ip_matches", True))
    independent_only = bool(cfg.get("independent_sellers_only", True))
    dropped: dict[str, int] = {}
    dropped_terms: set[str] = set()

    mover_pool = [c for c in movers[: int(cfg.get("enrich_candidate_pool", 50))]]
    entrant_pool = [c for c in new_entrants[: int(cfg.get("new_entrant_pool", 15))]]
    baseline_pool = [c for c in baseline_only[: int(cfg.get("new_entrant_pool", 15))]]
    lookup = {c.asin: c for c in mover_pool + entrant_pool + baseline_pool}

    print(f"Enrich stage mode={mode}; Keepa tokens at start={api.tokens_left}")
    print(f"Snapshot {meta['snapshot_date']}; 24h baseline {meta['baseline_24h']}; "
          f"7d baseline {meta['baseline_7d']}")

    # Known-IP ASINs stay in the cache purely so we can avoid re-querying them.
    known_ip = {a for a, r in cache.items()
                if r.get("ip_flags") or r.get("licensed_markers")} if block_ip else set()
    fetched = fetch_metadata(api, list(lookup), cache, refresh_days,
                             want_reviews=bool(cfg.get("fetch_reviews", True)),
                             skip=known_ip)
    now_utc = datetime.now(timezone.utc)

    def assemble(pool: list[Candidate]) -> list[dict[str, Any]]:
        out = []
        for c in pool:
            if c.asin in rejected:
                continue
            if block_ip and c.asin in known_ip:
                dropped["ip"] = dropped.get("ip", 0) + 1
                continue
            row = build_row(c.asin, fetched.get(c.asin), cache.get(c.asin, {}),
                            now_utc, ip_watchlist)
            if row is None:
                continue
            if c.asin in fetched:
                cache[c.asin] = row

            if title_is_blocked(row["title"], blocked_terms):
                continue
            if block_ip and row.get("ip_flags"):
                dropped["brand"] = dropped.get("brand", 0) + 1
                dropped_terms.update(row["ip_flags"])
                continue
            if independent_only and row.get("licensed_markers"):
                dropped["licensed"] = dropped.get("licensed", 0) + 1
                continue
            # Merch boilerplate is what proves the listing is print-on-demand
            # rather than a bulk-manufactured blank from an apparel company.
            if independent_only and int(row.get("merch_marker_hits") or 0) < 2:
                dropped["not_merch"] = dropped.get("not_merch", 0) + 1
                continue
            if not likely_tshirt(row["title"]):
                continue
            if require_amazon and not row.get("amazon_direct_offer"):
                continue
            if CONFIDENCE_ORDER.get(row.get("merch_confidence", "Low"), 0) < min_conf:
                continue

            out.append({
                **row,
                "amazon_url": f"https://www.amazon.com/dp/{c.asin}",
                "audience": c.audience,
                "category_position": c.category_position,
                "change_24h_pct": round(c.change_24h_pct, 1) if c.change_24h_pct is not None else None,
                "change_7d_pct": round(c.change_7d_pct, 1) if c.change_7d_pct is not None else None,
                "is_new_entrant": c.is_new_entrant,
                "blended_score": c.blended_score,
            })
        return out

    top_movers = assemble(mover_pool)[: int(cfg.get("final_count", 25))]
    top_new = assemble(entrant_pool)[: int(cfg.get("final_new_entrant_count", 10))]
    top_baseline = assemble(baseline_pool)[: int(cfg.get("final_new_entrant_count", 10))]

    # Title pool for keyword mining: freshly fetched titles plus everything in
    # cache. Built AFTER assembly so this run's fetches are included — otherwise
    # a wiped cache yields no siblings and every phrase list comes back empty.
    all_titles = [p.get("title", "") for p in fetched.values() if p.get("title")]
    all_titles += [r.get("title", "") for r in cache.values() if r.get("title")]
    all_titles = [t for t in dict.fromkeys(all_titles) if t]

    # Keyword mining: for each surfaced row, gather sibling titles that share the
    # niche's leading word and build a report + a paste-ready listing.
    def attach_keywords(rows):
        for r in rows:
            niche = r.get("slogan") or ""
            key = (niche.split() or [""])[0].lower()
            siblings = [t for t in all_titles if key and key in t.lower()] or [r.get("title", "")]
            rep = keyword_report(siblings)
            r["keyword_report"] = rep
            r["listing"] = suggested_listing(niche, rep)
    attach_keywords(top_movers)
    attach_keywords(top_new)
    attach_keywords(top_baseline)

    prune_products_cache(cache, int(cfg.get("products_cache_max_age_days", 60)))
    save_json(DATA / "products.json", cache)

    latest = {
        "generated_at": now_utc.isoformat(),
        "generated_at_pacific": datetime.now(PACIFIC).isoformat(),
        "mode": mode,
        "snapshot_meta": {**meta, "filtered": dropped,
                          "ip_filtered": sum(dropped.values()),
                          "ip_terms_hit": sorted(dropped_terms)},
        "niche_clusters": cluster_niches(top_movers),
        "products": top_movers,
        "new_entrants": top_new,
        "baseline_top": top_baseline,
        "methodology": {
            "discovery": "Daily Keepa Best Sellers snapshots from the exact Amazon novelty T-shirt browse nodes",
            "momentum": "Change in each ASIN's position within its daily category Best Sellers list",
            "new_entrants": "ASINs absent from the prior snapshot are listed separately and never receive an imputed momentum score",
            "enrichment": "Metadata is queried only for top candidates; no product-history query is required",
            "merch_detection": "Merch by Amazon boilerplate in the feature bullets, with a direct Amazon offer as a secondary confirm",
            "blended_score": "45% 24h percentile + 35% 7d percentile + 20% current-rank percentile, computed across the full category population and renormalized when a component is unavailable",
        },
    }
    save_json(DATA / "latest.json", latest)

    from src.dashboard import build_dashboard
    build_dashboard(latest)

    if dropped:
        print("Filtered out — " + ", ".join(
            f"{v} {k}" for k, v in sorted(dropped.items())))
        if dropped_terms:
            print(f"  brand/IP terms hit: {', '.join(sorted(dropped_terms))}")
    print(f"Enrich complete. Movers: {len(top_movers)}; new arrivals: {len(top_new)}; "
          f"baseline leaders: {len(top_baseline)}")
    print(f"Keepa tokens remaining: {api.tokens_left}")


def prune_products_cache(cache: dict[str, Any], max_age_days: int) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    for asin in list(cache):
        stamp = cache[asin].get("fetched_at")
        if not stamp:
            cache.pop(asin, None)
            continue
        try:
            fetched = datetime.fromisoformat(stamp)
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
            if fetched < cutoff:
                cache.pop(asin, None)
        except ValueError:
            cache.pop(asin, None)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["collect", "enrich"], required=True)
    parser.add_argument("--mode", choices=["quick", "full"], default="full")
    args = parser.parse_args()

    cfg = load_json(CONFIG_PATH, {})
    key = os.environ.get("KEEPA_API_KEY")
    if not key:
        raise SystemExit("KEEPA_API_KEY is missing")

    api = keepa.Keepa(key, timeout=30)
    if args.stage == "collect":
        collect_rankings(api, cfg, args.mode)
    else:
        enrich_dashboard(api, cfg, args.mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
