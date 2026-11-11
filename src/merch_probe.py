"""One-off probe: can Keepa's Product Finder surface Merch on Demand shirts early?

Runs ONE search (Merch on Demand listings in Amazon's Novelty T-Shirt
categories that are at most two weeks old, one row per design, best sales rank
first), then looks up as many of the results as the remaining token balance
allows and separates likely licensed merchandise from independent designs.
Nothing is written to the repo; the report goes to the Actions log and the
run summary.

    python -m src.merch_probe
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import keepa

KEEPA_EPOCH_OFFSET_MIN = 21564000  # Keepa minutes = unix minutes - this offset
# Two weeks, not two months: the point is to see shirts before they reach the
# top of the long-running best-seller views.
LISTED_WITHIN_DAYS = int(os.environ.get("PROBE_LISTED_WITHIN_DAYS", "14"))
MAX_RESULTS = int(os.environ.get("PROBE_MAX_RESULTS", "50"))
TOKEN_RESERVE = 2  # never spend the balance all the way down

# The first run (no category filter) returned only phone cases. These are the
# Novelty T-Shirt nodes for Men, Women, Girls and Boys, the same ones in config.json.
TSHIRT_CATEGORIES = [9056987011, 9056923011, 9057040011, 9057094011]

# Official brands seen in the second run that config.json's ip_watchlist misses.
EXTRA_LICENSED = [
    "journey", "project hail mary", "ghostbusters", "coca-cola", "coca cola",
    "grease", "ramones", "lynyrd skynyrd", "godzilla", "friday the 13th",
    "universal", "mademark", "michael jackson", "notre dame",
]
LICENSED_PHRASES = ["officially licensed", "official license", "official merch"]


def keepa_minutes(days_ago: int) -> int:
    return int(time.time() / 60) - KEEPA_EPOCH_OFFSET_MIN - days_ago * 24 * 60


def tokens(api: keepa.Keepa) -> int:
    api.update_status()  # free call; refreshes api.tokens_left
    return int(api.tokens_left)


def md_cell(text: str) -> str:
    return str(text).replace("|", "/").replace("\n", " ").strip()


def squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def licensed_terms() -> list[str]:
    terms = list(EXTRA_LICENSED)
    try:
        cfg = json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text())
        terms += [str(t) for t in cfg.get("ip_watchlist", [])]
    except (OSError, ValueError):
        pass  # the built-in list still applies
    return sorted({squash(t) for t in terms if len(squash(t)) >= 4})


def licensed_match(brand: str, title: str, terms: list[str]) -> str:
    """Return the term that marks a listing as likely licensed, or ''."""
    text = f"{brand} {title}".lower()
    for phrase in LICENSED_PHRASES:
        if phrase in text:
            return phrase
    squashed = squash(text)
    for term in terms:
        if term in squashed:
            return term
    return ""


def age_days(listed_since) -> str:
    """Listing age from Keepa's listedSince (Keepa minutes); '?' if unusable."""
    if not isinstance(listed_since, (int, float)) or listed_since <= 0:
        return "?"
    days = (keepa_minutes(0) - listed_since) / (24 * 60)
    return f"{days:.0f}" if days >= 0 else "?"


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
        listedSince_gte=keepa_minutes(LISTED_WITHIN_DAYS),
        current_SALES_gte=1,  # must have a sales rank
        sort=[["current_SALES", "asc"]],  # best rank first
        perPage=MAX_RESULTS,
    )
    print(f"Search: Merch on Demand novelty T-shirts, one per design, listed in last "
          f"{LISTED_WITHIN_DAYS} days, best rank first")
    asins = api.product_finder(params, domain="US", n_products=MAX_RESULTS)
    after_search = tokens(api)
    print(f"Search returned {len(asins)} ASINs; cost about {start - after_search} tokens; "
          f"tokens now: {after_search}")

    lines = [
        "## Merch on Demand probe: shirts listed in the last "
        f"{LISTED_WITHIN_DAYS} days",
        "",
        f"- Search returned **{len(asins)}** listings (asked for up to {MAX_RESULTS})",
        f"- Tokens at start: **{start}**; search cost about **{start - after_search}**",
    ]

    if not asins:
        lines.append("- **No results.** The filter combination returned nothing.")
        report(lines)
        return 0

    affordable = max(0, min(len(asins), after_search - TOKEN_RESERVE))
    terms = licensed_terms()
    rows = {}
    if affordable:
        print(f"Looking up {affordable} of {len(asins)} listings (1 token each)")
        products = api.query(asins[:affordable], domain="US", history=False,
                             stats=1, progress_bar=False, wait=False)
        for p in products:
            current = (p.get("stats_parsed") or {}).get("current", {})
            title, brand = p.get("title") or "", p.get("brand") or ""
            rows[p.get("asin")] = {
                "title": title,
                "brand": brand,
                "rank": current.get("SALES"),
                "age": age_days(p.get("listedSince")),
                "licensed": licensed_match(brand, title, terms),
            }
    end = tokens(api)
    lines.append(f"- Looked up **{len(rows)}** listings; tokens left: **{end}** "
                 "(Keepa's balance can lag by a minute)")

    independent = [a for a in asins if a in rows and not rows[a]["licensed"]]
    licensed = [a for a in asins if a in rows and rows[a]["licensed"]]
    skipped = [a for a in asins if a not in rows]
    tees = sum(1 for r in rows.values() if "t-shirt" in r["title"].lower())
    if rows:
        lines.append(f"- **{tees} of {len(rows)}** looked-up titles contain \"T-Shirt\"")
        lines.append(f"- **{len(independent)}** look independent; **{len(licensed)}** "
                     "look licensed (matched a known brand or \"officially licensed\")")

    def link(asin: str) -> str:
        return f"[{asin}](https://www.amazon.com/dp/{asin})"

    def rank(r: dict) -> str:
        return f"{int(r['rank']):,}" if r["rank"] else "?"

    if independent:
        lines += ["", "### Likely independent designs", "",
                  "| # | Rank | Days old | Brand | Title | Link |", "|---|---|---|---|---|---|"]
        for i, asin in enumerate(independent, 1):
            r = rows[asin]
            lines.append(f"| {i} | {rank(r)} | {r['age']} | {md_cell(r['brand'])[:30]} | "
                         f"{md_cell(r['title'])[:90]} | {link(asin)} |")
    if licensed:
        lines += ["", "### Likely licensed (set aside)", "",
                  "| Rank | Days old | Matched | Brand | Title | Link |", "|---|---|---|---|---|---|"]
        for asin in licensed:
            r = rows[asin]
            lines.append(f"| {rank(r)} | {r['age']} | {md_cell(r['licensed'])} | "
                         f"{md_cell(r['brand'])[:30]} | {md_cell(r['title'])[:70]} | {link(asin)} |")
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
