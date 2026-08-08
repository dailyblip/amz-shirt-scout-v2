# Amazon Shirt Scout

Tracks Amazon US novelty T-shirt Best Sellers via Keepa and surfaces shirts worth designing against, while staying inside an entry-tier Keepa token budget.

## How it works

Two stages run as separate jobs so a token refill wait never stalls one run:

1. **Collect** — stores today's ranked ASIN list per audience. One Best Sellers query per category, no product queries.
2. **Enrich** — scores momentum from the stored snapshots, then queries metadata for only the top candidates and rebuilds the dashboard.

Momentum comes from our own daily snapshots, so no per-product Keepa history query is ever needed.

## Setup

1. Push this repo to GitHub.
2. Settings → Secrets and variables → Actions → add `KEEPA_API_KEY`.
3. Settings → Pages → serve from the `site/` directory (or point Vercel at it).

### First run

Actions → **Daily Shirt Scout** → Run workflow:

- Stage `collect`, mode `quick`

Wait for tokens to refill, then run stage `enrich`, mode `quick`. The dashboard will be sparse: momentum needs **two** collect runs, and the 7-day column needs **eight**. That's expected, not a bug.

After that, leave the schedules on. Collect fires at 07:00 UTC, enrich at 12:00 UTC, so the board is ready before 6 AM Pacific.

### Testing without an API key

```bash
pip install -r requirements.txt
python -m tests.test_offline
```

Fabricates nine days of snapshots and a fake Keepa client, runs a full enrich, and asserts on prices, review counts, baseline selection, and new-entrant separation. Then open `site/index.html`.

## Scoring

**Climbing** — ASINs present in both the current and prior snapshot. Score is 45% 24-hour percentile + 35% 7-day percentile + 20% current-rank percentile, computed across the *entire* tracked population rather than the enriched subset, so a score of 82 means the same thing on any given day. When a component has no data, the remaining weights renormalize instead of defaulting to a neutral value.

**New arrivals** — ASINs absent from the prior snapshot, listed separately and ranked by current position only. Nothing about their momentum is inferred. This is deliberate: imputing a prior rank of `depth + 1` makes every list-churn artifact from the bottom of the top 500 look like a 60% gainer and drowns out real movers.

## Merch by Amazon detection

Primary signal is Merch's boilerplate feature bullets, which are byte-identical across essentially every Merch listing:

- `Solid colors: 100% Cotton; Heather Grey: 90% Cotton, 10% Polyester`
- `Lightweight, Classic fit, Double-needle sleeve and bottom hem`

Two or more markers → High. One marker, or a direct Amazon offer on a T-shirt title → Medium. `min_merch_confidence` in `config.json` sets the floor for inclusion. Validate against real listings on your first live run and adjust.

## Trademark

`blocked_terms` screens profanity and politics, which is not the risk that gets Merch accounts suspended. Trademark infringement is. Every brief shows the extracted slogan with a link to the USPTO search — check it in class 025 (clothing) before spending design time. Trending phrases are exactly the ones most likely to be claimed.

## Data files

- `data/snapshots/YYYY-MM-DD.json` — one file per day, pruned to `snapshot_retention_days`
- `data/products.json` — cached metadata, pruned to `products_cache_max_age_days`
- `data/latest.json` — current dashboard payload
- `data/collect_status.json` — last collect result, including any failed audiences
- `data/rejected.json` — persistent rejections (currently read-only; the UI still uses localStorage)

## Config

| Key | Purpose |
| --- | --- |
| `snapshot_depth` | ASINs tracked per category per day |
| `enrich_candidate_pool` | Movers sent for metadata lookup (≈1 token each) |
| `new_entrant_pool` | New arrivals sent for metadata lookup |
| `metadata_refresh_days` | Cache TTL before re-querying an ASIN |
| `require_amazon_offer` | Drop ASINs with no live direct Amazon offer |
| `min_merch_confidence` | `Low` / `Medium` / `High` inclusion floor |

Token cost per enrich run is roughly `enrich_candidate_pool + new_entrant_pool` minus cache hits — about 65 on the defaults, dropping sharply once the cache warms.

## Known limitations

- Rejections are browser-local; `data/rejected.json` is read but never written.
- Category browse-node IDs are hardcoded and Amazon changes them occasionally. If an audience returns nothing, check `collect_status.json` for `audiences_failed`.
- Category position is not BSR. It's rank within the novelty T-shirt node, which is the right signal here but isn't comparable to a headline BSR number.
- Google login, email delivery, and image generation are not built.
