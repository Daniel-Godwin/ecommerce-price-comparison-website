"""Local data collector — feeds REAL retailer data to the production database.

Why this exists: retailers like Jumia serve normal Nigerian connections but
block cloud datacenter IPs (e.g., Render's). So the deployed site can't
scrape live — but YOUR machine can. This script runs the full search
pipeline (live adapters -> normalize -> persist -> embed) against the
PRODUCTION database, from your residential connection.

Usage (PowerShell, from the project folder):

    $env:DATABASE_URL = "<External Database URL from the Render dashboard>"
    python -m jobs.collector                 # one pass over the query list
    python -m jobs.collector --loop 86400    # repeat daily (recommended)

Get the External Database URL: Render dashboard -> pricecompare-db ->
Connect -> External Database URL. Never commit it; set it per-session or
put it in .env as COLLECTOR_DATABASE_URL.

Edit QUERIES below to control what the public site tracks.

Politeness: every retailer request is spaced 10-15s apart by the adapter
layer itself, and queries are separated by 20-40s here. A full pass takes
~10 minutes and looks like a person browsing — this keeps your home IP
from being flagged by retailer bot protection. Run at most once a day,
ideally at night (see README for Windows Task Scheduler setup).
"""
from __future__ import annotations

import argparse
import logging
import os
import random
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("collector")

QUERIES = [
    "samsung galaxy a15",
    "samsung galaxy a25",
    "infinix hot 40",
    "tecno spark 20",
    "redmi note 13",
    "hp laptop",
    "dell laptop",
    "bluetooth speaker",
    "power bank 20000mah",
    "smart tv 43 inch",
    "rice cooker",
    "standing fan",
]


def run_once() -> None:
    # allow a dedicated env var so local dev .env (sqlite) isn't disturbed
    prod_url = os.getenv("COLLECTOR_DATABASE_URL")
    if prod_url:
        os.environ["DATABASE_URL"] = prod_url
    if "render.com" not in os.getenv("DATABASE_URL", "") and \
       not os.getenv("COLLECTOR_ALLOW_ANY_DB"):
        logger.warning(
            "DATABASE_URL does not look like a Render URL — collecting into "
            "the LOCAL database. Set COLLECTOR_DATABASE_URL to the External "
            "Database URL from Render to feed the live site."
        )

    from app.core.search_service import execute_search
    from app.db.session import db_session, init_db
    from app.schemas.api import SearchRequest

    init_db()
    ok = fail = 0
    with db_session() as db:
        for q in QUERIES:
            try:
                result = execute_search(
                    db,
                    SearchRequest(query=q, retailers=["jumia", "konga"],
                                  use_cache=False),
                )
                live = sum(s.listings_found for s in result.sources_status if s.ok)
                logger.info("%-28s -> %3d live listings", q, live)
                ok += 1
            except Exception as exc:  # noqa: BLE001 — keep collecting
                logger.warning("%-28s -> failed: %s", q, exc)
                fail += 1
            time.sleep(random.uniform(20, 40))   # human-like pacing
    logger.info("collection pass done: %d ok, %d failed", ok, fail)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", type=int, default=0,
                        help="repeat every N seconds (0 = run once)")
    args = parser.parse_args()
    while True:
        run_once()
        if not args.loop:
            break
        logger.info("sleeping %ds…", args.loop)
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
