"""One-off probe: can Keepa's Product Finder pick out Merch shirts that are moving?

Runs ONE search (Merch on Demand listings in Amazon's Novelty T-Shirt
categories, listed 10 to 60 days ago, with several sales in the last 30 days,
one row per design), then looks up as many results as the token balance
allows. For each it compares today's sales rank with its 30-day average, sets
aside licensed merchandise and blocked topics, and lists the rest with the
fastest climbers first. Nothing is written to the repo; the report goes to the
Actions log and the run summary.

    python -m src.merch_probe
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

import keepa

KEEPA_EPOCH_OFFSET_MIN = 21564000  # Keepa minutes = unix minutes - this offset
# Old enough to have a trend, young enough that the niche is not crowded yet.
LISTED_MIN_DAYS = int(os.environ.get("PROBE_LISTED_MIN_DAYS", "10"))
LISTED_MAX_DAYS = int(os.environ.get("PROBE_LISTED_MAX_DAYS", "60"))
# Keepa counts each upward jump in sales rank; one jump is roughly one sale.
MIN_SALES_30D = int(os.environ.get("PROBE_MIN_SALES_30D", "5"))
MAX_RESULTS = int(os.environ.get("PROBE_MAX_RESULTS", "50"))
TOKEN_RESERVE = 2  # never spend the balance all the way down

# The first run (no category filter) returned only phone cases. These are the
# Novelty T-Shirt nodes for Men, Women, Girls and Boys, the same ones in config.json.
TSHIRT_CATEGORIES = [9056987011, 9056923011, 9057040011, 9057094011]

# Official brands seen in earlier runs that config.json's ip_watchlist misses.
EXTRA_LICENSED = [
    "project hail mary", "ghostbusters", "coca-cola", "coca cola", "ramones",
    "lynyrd skynyrd", "godzilla", "friday the 13th", "mademark", "michael jackson",
    "notre dame", "jeff dunham", "macy's", "macys", "mlb", "mlbpa", "nfl", "nba",
    "nhl", "pokemon",
]
# Ordinary words that are also official brands: only count them as the brand name.
BRAND_ONLY_LICENSED = ["journey", "grease", "universal"]
LICENSED_PHRASES = ["officially licensed", "official license", "official merch"]
# config.json blocks a few political words; news-driven election shirts use others.
EXTRA_BLOCKED = [
    "midterm", "midterms", "vote", "voter", "governor", "senator", "senate",
    "congress", "political", "politics", "veto", "blue wave", "blue tsunami",
    "red wave", "democrats", "republicans",
]


def keepa_minutes(days_ago: int) -> int:
    return int(time.time() / 60) - KEEPA_EPOCH_OFFSET_MIN - days_ago * 24 * 60


def tokens(api: keepa.Keepa) -> int:
    api.update_status()  # free call; refreshes api.tokens_left
    return int(api.tokens_left)


def md_cell(text: str) -> str:
    return str(text).replace("|", "/").replace("\n", " ").strip()


def words(text: str) -> str:
    """Lowercase, strip accents, and turn punctuation into spaces: ' pokemon squirtle '."""
    plain = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return " " + re.sub(r"[^a-z0-9]+", " ", plain.lower()).strip() + " "


def load_config() -> dict:
    try:
        return json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text())
    except (OSError, ValueError):
        return {}  # the built-in lists still apply


def term_list(extra: list[str], from_config: list) -> list[str]:
    return sorted({words(t) for t in [*extra, *map(str, from_config)] if words(t).strip()})


def first_match(text: str, terms: list[str]) -> str:
    """Return the first whole-word term found in text, or ''."""
    padded = words(text)
    for term in terms:
        if term in padded:
            return term.strip()
    return ""


def licensed_match(brand: str, title: str, terms: list[str]) -> str:
    text = f"{brand} {title}"
    for phrase in LICENSED_PHRASES:
        if phrase in text.lower():
            return phrase
    hit = first_match(text, terms)
    if hit:
        return hit
    # Brands are often written without spaces ("STARWARS"), so also compare squashed.
    squashed_brand = words(brand).replace(" ", "")
    for term in terms:
        squashed = term.replace(" ", "")
        if len(squashed) >= 6 and squashed in squashed_brand:
            return term.strip()
    return first_match(brand, [words(t) for t in BRAND_ONLY_LICENSED])


def age_days(listed_since) -> str:
    """Listing age from Keepa's listedSince (Keepa minutes); '?' if unusable."""
    if not isinstance(listed_since, (int, float)) or listed_since <= 0:
        return "?"
    days = (keepa_minutes(0) - listed_since) / (24 * 60)
    return f"{days:.0f}" if days >= 0 else "?"


def as_rank(value) -> int | None:
    try:
        rank = int(value)
    except (TypeError, ValueError):
        return None
    return rank if rank > 0 else None


def main() -> int:
    key = os.environ.get("KEEPA_API_KEY", "").strip()
    if not key:
        print("KEEPA_API_KEY is missing.")
        return 1

    api = keepa.Keepa(key, timeout=30)
    start = tokens(api)
    print(f"Tokens at start: {start}")

    params = keepa.ProductParams(
        isMerchOnDemand=True,
        categories_include=TSHIRT_CATEGORIES,
        singleVariation=True,  # one row per design, not one per size/colour
        listedSince_gte=keepa_minutes(LISTED_MAX_DAYS),
        listedSince_lte=keepa_minutes(LISTED_MIN_DAYS),
        salesRankDrops30_gte=MIN_SALES_30D,
        current_SALES_gte=1,  # must have a sales rank
        sort=[["current_SALES", "asc"]],  # best rank first
        perPage=MAX_RESULTS,
    )
    what = (f"Merch on Demand novelty T-shirts, one per design, listed {LISTED_MIN_DAYS} to "
            f"{LISTED_MAX_DAYS} days ago, at least {MIN_SALES_30D} sales signals in 30 days")
    print(f"Search: {what}")
    asins = api.product_finder(params, domain="US", n_products=MAX_RESULTS)
    after_search = tokens(api)
    print(f"Search returned {len(asins)} ASINs; cost about {start - after_search} tokens; "
          f"tokens now: {after_search}")

    lines = [
        "## Merch on Demand probe: shirts that are moving",
        "",
        f"- Search: {what}",
        f"- Search returned **{len(asins)}** listings (asked for up to {MAX_RESULTS})",
        f"- Tokens at start: **{start}**; search cost about **{start - after_search}**",
    ]

    if not asins:
        lines.append("- **No results.** The filter combination returned nothing.")
        report(lines)
        return 0

    cfg = load_config()
    licensed_terms = term_list(EXTRA_LICENSED, cfg.get("ip_watchlist", []))
    blocked_terms = term_list(EXTRA_BLOCKED, cfg.get("blocked_terms", []))

    affordable = max(0, min(len(asins), after_search - TOKEN_RESERVE))
    rows = {}
    if affordable:
        print(f"Looking up {affordable} of {len(asins)} listings (1 token each)")
        products = api.query(asins[:affordable], domain="US", history=False,
                             stats=30, progress_bar=False, wait=False)
        for p in products:
            parsed = p.get("stats_parsed") or {}
            raw = p.get("stats") or {}
            rank = as_rank((parsed.get("current") or {}).get("SALES"))
            avg30 = as_rank((parsed.get("avg30") or {}).get("SALES"))
            sales = raw.get("salesRankDrops30", parsed.get("salesRankDrops30"))
            title, brand = p.get("title") or "", p.get("brand") or ""
            licensed = licensed_match(brand, title, licensed_terms)
            blocked = "" if licensed else first_match(f"{brand} {title}", blocked_terms)
            rows[p.get("asin")] = {
                "title": title,
                "brand": brand,
                "rank": rank,
                "avg30": avg30,
                # Positive = today's rank is better (lower) than the 30-day average.
                "climb": round(100 * (avg30 - rank) / avg30) if rank and avg30 else None,
                "sales": sales if isinstance(sales, int) and sales >= 0 else None,
                "age": age_days(p.get("listedSince")),
                "aside": f"licensed: {licensed}" if licensed else
                         f"blocked topic: {blocked}" if blocked else "",
            }
    end = tokens(api)
    lines.append(f"- Looked up **{len(rows)}** listings; tokens left: **{end}** "
                 "(Keepa's balance can lag by a minute)")

    kept = [a for a in asins if a in rows and not rows[a]["aside"]]
    aside = [a for a in asins if a in rows and rows[a]["aside"]]
    skipped = [a for a in asins if a not in rows]
    # Fastest climbers first; listings with no 30-day average go last.
    kept.sort(key=lambda a: (rows[a]["climb"] is None, -(rows[a]["climb"] or 0)))
    climbing = sum(1 for a in kept if (rows[a]["climb"] or 0) > 0)
    if rows:
        lines.append(f"- **{len(kept)}** kept; **{len(aside)}** set aside (licensed or blocked topic)")
        lines.append(f"- **{climbing} of {len(kept)}** kept shirts rank better today than their "
                     "30-day average")

    def link(asin: str) -> str:
        return f"[{asin}](https://www.amazon.com/dp/{asin})"

    def num(value) -> str:
        return f"{value:,}" if value is not None else "?"

    def climb(r: dict) -> str:
        return f"{r['climb']:+d}%" if r["climb"] is not None else "?"

    if kept:
        lines += ["", "### Kept, fastest climbers first", "",
                  "Climb = how much better today's rank is than the 30-day average. "
                  "Sales (30d) = Keepa's count of rank jumps, roughly one per sale.", "",
                  "| # | Climb | Rank now | 30-day avg | Sales (30d) | Days old | Brand | Title | Link |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for i, asin in enumerate(kept, 1):
            r = rows[asin]
            lines.append(f"| {i} | {climb(r)} | {num(r['rank'])} | {num(r['avg30'])} | "
                         f"{num(r['sales'])} | {r['age']} | {md_cell(r['brand'])[:30]} | "
                         f"{md_cell(r['title'])[:90]} | {link(asin)} |")
    if aside:
        lines += ["", "### Set aside", "",
                  "| Why | Climb | Rank now | Sales (30d) | Brand | Title | Link |",
                  "|---|---|---|---|---|---|---|"]
        for asin in aside:
            r = rows[asin]
            lines.append(f"| {md_cell(r['aside'])} | {climb(r)} | {num(r['rank'])} | "
                         f"{num(r['sales'])} | {md_cell(r['brand'])[:30]} | "
                         f"{md_cell(r['title'])[:70]} | {link(asin)} |")
    if skipped:
        lines += ["", f"Not looked up ({len(skipped)}): " + ", ".join(link(a) for a in skipped)]
    report(lines)
    return 0


def report(lines: list[str]) -> None:
    text = "\n".join(lines)
    print("\n" + text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


if __name__ == "__main__":
    sys.exit(main())
