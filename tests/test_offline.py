"""Offline smoke test: fabricates snapshots and a fake Keepa client.

Run with:  python -m tests.test_offline
No API key or network access required.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta

from src import scout
from src.scout import DATA, PACIFIC, save_json, snapshot_path

MERCH_FEATURES = [
    "Lightweight, Classic fit, Double-needle sleeve and bottom hem",
    "Solid colors: 100% Cotton; Heather Grey: 90% Cotton, 10% Polyester",
]


def make_snapshots(days: int = 9, depth: int = 120) -> None:
    random.seed(7)
    today = datetime.now(PACIFIC).date()
    universe = [f"B0TEST{i:04d}" for i in range(depth + 40)]

    for back in range(days - 1, -1, -1):
        day = today - timedelta(days=back)
        pool = universe[:depth] if back > 0 else universe[20:depth + 20]
        shuffled = pool[:]
        random.shuffle(shuffled)
        payload = {"Men": {asin: i + 1 for i, asin in enumerate(shuffled)}}
        save_json(snapshot_path(day), payload)
    print(f"Wrote {days} snapshots ending {today}")


class FakeKeepa:
    tokens_left = 300

    def query(self, asins, **kwargs):
        random.seed(11)
        out = []
        for i, asin in enumerate(asins):
            merch = i % 3 != 0
            out.append({
                "asin": asin,
                "title": f"Funny Volunteer Committee Sixteen Squad T-Shirt #{i}",
                "brand": "Test Brand",
                "manufacturer": "Test Brand",
                "imagesCSV": "51abcdefgh.jpg,52ijkl.jpg",
                "features": MERCH_FEATURES if merch else ["Imported", "Machine wash"],
                # stats_parsed prices are ALREADY in dollars.
                "stats_parsed": {"current": {
                    "AMAZON": 19.99 if i % 4 != 0 else None,
                    "COUNT_REVIEWS": 100 + i * 7,
                }},
            })
        return out

    def best_sellers_query(self, *a, **k):
        raise AssertionError("collect stage should not run in this test")


def main() -> None:
    make_snapshots()
    cfg = scout.load_json(scout.CONFIG_PATH, {})
    scout.enrich_dashboard(FakeKeepa(), cfg, "quick")

    latest = json.loads((DATA / "latest.json").read_text())
    meta = latest["snapshot_meta"]
    print("\n--- assertions ---")
    assert meta["baseline_24h"] != meta["snapshot_date"], "24h baseline must not be today"
    assert meta["baseline_7d"] and meta["baseline_7d"] != meta["snapshot_date"]
    assert latest["products"], "expected movers"
    assert latest["new_entrants"], "expected new arrivals"

    for p in latest["products"]:
        assert p["price"] is None or 1 < p["price"] < 1000, f"bad price {p['price']}"
        assert p["reviews"] is not None, "reviews should be populated"
        assert p["merch_confidence"] in ("Medium", "High")
        assert p["change_24h_pct"] is not None or p["change_7d_pct"] is not None
        assert not p["is_new_entrant"], "movers table must exclude new entrants"

    for p in latest["new_entrants"]:
        assert p["is_new_entrant"]

    # "Sixteen"/"Committee"/"Volunteer" must not be what qualified these as tees.
    assert scout.likely_tshirt("Sixteen Committee Volunteer Canteen") is False
    assert scout.likely_tshirt("Cool Tshirt For Dads") is True
    assert scout.likely_tshirt("Retro Graphic Tees") is True
    assert scout.normalize_money(19.99) == 19.99
    assert scout.normalize_money(None) is None

    top = latest["products"][0]
    print(f"top mover: score={top['blended_score']} pos={top['category_position']} "
          f"24h={top['change_24h_pct']} 7d={top['change_7d_pct']} "
          f"price={top['price']} reviews={top['reviews']} merch={top['merch_confidence']}")
    print(f"movers={len(latest['products'])} new_arrivals={len(latest['new_entrants'])}")
    print(f"slogan sample: {top['slogan']!r}")
    print("ALL ASSERTIONS PASSED")


if __name__ == "__main__":
    main()
