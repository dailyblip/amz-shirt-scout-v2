"""Daily tracker: which Merch on Demand shirts are climbing day over day?

Runs the same single search and lookups as src.merch_probe, saves today's
ranks to data/merch_tracker/snapshots/YYYY-MM-DD.json, and compares them with
earlier days. The report goes to the run summary and data/merch_tracker/latest.md.

One run spends nearly the whole 60-token Keepa bucket, so run it once a day
and not within an hour of the manual Merch Probe.

    python -m src.merch_tracker
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import keepa

from src.merch_probe import collect, link, md_cell, num, pct, report, search_label

TRACK_DIR = Path(__file__).resolve().parent.parent / "data" / "merch_tracker"
SNAP_DIR = TRACK_DIR / "snapshots"
KEEP_SNAPSHOTS = 45


def load_snapshots() -> list[dict]:
    """All saved days, oldest first. Unreadable files are skipped."""
    days = []
    for path in sorted(SNAP_DIR.glob("*.json")):
        try:
            snap = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print(f"Skipping unreadable snapshot {path.name}")
            continue
        if isinstance(snap.get("rows"), dict) and snap.get("date"):
            days.append(snap)
    return sorted(days, key=lambda s: s["date"])


def change(old: int | None, new: int | None) -> int | None:
    """Percent improvement in rank from old to new; positive = climbing."""
    if not old or not new:
        return None
    return round(100 * (old - new) / old)


def history(asin: str, parent: str | None, days: list[dict]) -> list[tuple[str, int]]:
    """(date, rank) for every saved day that has a rank for this design.

    A design matches on its own ASIN or on its parent ASIN, because the search
    can return a different size or colour of the same shirt from day to day.
    """
    out = []
    for snap in days:
        rows = snap["rows"]
        row = rows.get(asin)
        if row is None and parent:
            row = next((r for a, r in rows.items() if parent in (a, r.get("parent"))), None)
        if row and row.get("rank"):
            out.append((snap["date"], row["rank"]))
    return out


def rising_streak(ranks: list[int]) -> int:
    """How many of the most recent day-to-day steps in a row were improvements."""
    streak = 0
    for older, newer in zip(reversed(ranks[:-1]), reversed(ranks[1:])):
        if newer < older:
            streak += 1
        else:
            break
    return streak


def build_report(today: dict, earlier: list[dict]) -> list[str]:
    rows, asins = today["rows"], today["asins"]
    kept = [a for a in asins if a in rows and not rows[a]["aside"]]
    aside = [a for a in asins if a in rows and rows[a]["aside"]]
    days = earlier + [today]

    tracked, new = [], []
    for asin in kept:
        past = history(asin, rows[asin].get("parent"), days)
        if len(past) < 2:
            new.append(asin)
            continue
        ranks = [r for _, r in past]
        tracked.append({
            "asin": asin,
            "first_date": past[0][0],
            "first_rank": ranks[0],
            "prev_rank": ranks[-2],
            "day": change(ranks[-2], ranks[-1]),
            "total": change(ranks[0], ranks[-1]),
            "streak": rising_streak(ranks),
            "days": len(ranks),
        })
    tracked.sort(key=lambda t: (-t["streak"], -(t["total"] or 0)))
    rising = [t for t in tracked if t["streak"] >= 1]
    falling = [t for t in tracked if t["streak"] == 0]

    lines = [
        f"## Merch tracker: {today['date']}",
        "",
        f"- Search: {search_label()}",
        f"- Tokens at start: **{today['tokens']['start']}**; search cost about "
        f"**{today['tokens']['search_cost']}**; left: **{today['tokens']['end']}**",
        f"- Looked up **{len(rows)}** of {len(asins)} listings: **{len(kept)}** kept, "
        f"**{len(aside)}** set aside (licensed or blocked topic)",
        f"- Days on record: **{len(days)}**",
        f"- **{len(rising)}** rising since the previous day, **{len(falling)}** flat or falling, "
        f"**{len(new)}** new today",
    ]
    if len(days) < 2:
        lines.append("- This is the first day on record, so there is nothing to compare yet.")

    def tracked_table(title: str, items: list[dict]) -> None:
        if not items:
            return
        lines.extend([
            "", f"### {title}", "",
            "| Days rising | Since yesterday | Since first seen | Rank now | Rank yesterday | "
            "First seen | Sales (30d) | Title | Link |",
            "|---|---|---|---|---|---|---|---|---|"])
        for t in items:
            r = rows[t["asin"]]
            lines.append(
                f"| {t['streak']} | {pct(t['day'])} | {pct(t['total'])} | {num(r['rank'])} | "
                f"{num(t['prev_rank'])} | {num(t['first_rank'])} on {t['first_date']} | "
                f"{num(r['sales'])} | {md_cell(r['title'])[:80]} | {link(t['asin'])} |")

    tracked_table("Rising (longest streak first)", rising)
    tracked_table("Flat or falling", falling)

    if new:
        new.sort(key=lambda a: (rows[a]["climb"] is None, -(rows[a]["climb"] or 0)))
        lines += ["", "### New today", "",
                  "Climb = today's rank against the shirt's own 30-day average.", "",
                  "| Climb | Rank now | Sales (30d) | Days old | Title | Link |",
                  "|---|---|---|---|---|---|"]
        for asin in new:
            r = rows[asin]
            lines.append(f"| {pct(r['climb'])} | {num(r['rank'])} | {num(r['sales'])} | "
                         f"{r['age']} | {md_cell(r['title'])[:80]} | {link(asin)} |")

    if earlier:
        before = earlier[-1]["rows"]
        seen = set(rows) | {r.get("parent") for r in rows.values()}
        gone = [a for a, r in before.items()
                if not r.get("aside") and a not in seen and r.get("parent") not in seen]
        if gone:
            lines += ["", f"### No longer in the search ({len(gone)})", "",
                      "In the previous day's list but not looked up today: "
                      + ", ".join(f"{md_cell(before[a].get('title', ''))[:40]} ({link(a)})"
                                  for a in gone)]
    return lines


def prune() -> None:
    for path in sorted(SNAP_DIR.glob("*.json"))[:-KEEP_SNAPSHOTS]:
        path.unlink()


def main() -> int:
    key = os.environ.get("KEEPA_API_KEY", "").strip()
    if not key:
        print("KEEPA_API_KEY is missing.")
        return 1

    result = collect(keepa.Keepa(key, timeout=30))
    if not result["rows"]:
        # Saving an empty day would make every tracked shirt look like it vanished.
        report([
            "## Merch tracker",
            "",
            f"- Tokens at start: **{result['start']}**; search returned "
            f"**{len(result['asins'])}** listings; none could be looked up.",
            "- **Nothing saved today.** Most likely the token balance was too low.",
        ])
        return 1

    now = datetime.now(timezone.utc)
    today = {
        "date": now.strftime("%Y-%m-%d"),
        "run_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "search": search_label(),
        "tokens": {k: result[k] for k in ("start", "search_cost", "end")},
        "asins": result["asins"],
        "rows": result["rows"],
    }
    earlier = [s for s in load_snapshots() if s["date"] < today["date"]]

    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    (SNAP_DIR / f"{today['date']}.json").write_text(
        json.dumps(today, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    prune()

    lines = build_report(today, earlier)
    (TRACK_DIR / "latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    report(lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
