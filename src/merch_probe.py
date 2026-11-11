"""One-off probe: can Keepa's Product Finder return Merch on Demand shirts directly?

Runs ONE search (new Merch on Demand listings, best sales rank first), then looks
up as many of the results as the remaining token balance allows. Nothing is
written to the repo; the report goes to the Actions log and the run summary.

    python -m src.merch_probe
"""
from __future__ import annotations

import os
import sys
import time

import keepa

KEEPA_EPOCH_OFFSET_MIN = 21564000  # Keepa minutes = unix minutes - this offset
LISTED_WITHIN_DAYS = int(os.environ.get("PROBE_LISTED_WITHIN_DAYS", "60"))
MAX_RESULTS = int(os.environ.get("PROBE_MAX_RESULTS", "50"))
TOKEN_RESERVE = 2  # never spend the balance all the way down


def keepa_minutes(days_ago: int) -> int:
    return int(time.time() / 60) - KEEPA_EPOCH_OFFSET_MIN - days_ago * 24 * 60


def tokens(api: keepa.Keepa) -> int:
    api.update_status()  # free call; refreshes api.tokens_left
    return int(api.tokens_left)


def md_cell(text: str) -> str:
    return str(text).replace("|", "/").replace("\n", " ").strip()


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
        listedSince_gte=keepa_minutes(LISTED_WITHIN_DAYS),
        current_SALES_gte=1,  # must have a sales rank
        sort=[["current_SALES", "asc"]],  # best rank first
        perPage=MAX_RESULTS,
    )
    print(f"Search: Merch on Demand, listed in last {LISTED_WITHIN_DAYS} days, best rank first")
    asins = api.product_finder(params, domain="US", n_products=MAX_RESULTS)
    after_search = tokens(api)
    print(f"Search returned {len(asins)} ASINs; cost about {start - after_search} tokens; "
          f"tokens now: {after_search}")

    lines = [
        "## Merch on Demand probe",
        "",
        f"- Search returned **{len(asins)}** listings (asked for up to {MAX_RESULTS})",
        f"- Tokens at start: **{start}**; search cost about **{start - after_search}**",
    ]

    if not asins:
        lines.append("- **No results.** The filter combination returned nothing.")
        report(lines)
        return 0

    affordable = max(0, min(len(asins), after_search - TOKEN_RESERVE))
    rows = {}
    if affordable:
        print(f"Looking up {affordable} of {len(asins)} listings (1 token each)")
        products = api.query(asins[:affordable], domain="US", history=False,
                             stats=1, progress_bar=False, wait=False)
        for p in products:
            current = (p.get("stats_parsed") or {}).get("current", {})
            rows[p.get("asin")] = {
                "title": p.get("title") or "",
                "brand": p.get("brand") or "",
                "rank": current.get("SALES"),
            }
    end = tokens(api)
    lines.append(f"- Looked up **{len(rows)}** listings; tokens left: **{end}**")

    tees = sum(1 for r in rows.values() if "t-shirt" in r["title"].lower())
    if rows:
        lines.append(f"- **{tees} of {len(rows)}** looked-up titles contain \"T-Shirt\"")
    lines += ["", "| # | Rank | Brand | Title | Link |", "|---|---|---|---|---|"]
    for i, asin in enumerate(asins, 1):
        r = rows.get(asin)
        link = f"[{asin}](https://www.amazon.com/dp/{asin})"
        if r:
            rank = f"{int(r['rank']):,}" if r["rank"] else "?"
            lines.append(f"| {i} | {rank} | {md_cell(r['brand'])[:30]} | "
                         f"{md_cell(r['title'])[:90]} | {link} |")
        else:
            lines.append(f"| {i} | not looked up | | | {link} |")
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
