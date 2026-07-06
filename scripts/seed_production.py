"""Seed the PRODUCTION database with real retailer data from your machine.

Why this exists: cloud datacenter IPs are often blocked by retailers, but
your local (residential) network is not. This script runs the live search
pipeline ON YOUR MACHINE while writing to the production database, so the
deployed site serves real products and prices. The production API rebuilds
its vector index from the DB automatically on next restart/deploy.

Usage (PowerShell):
    $env:DATABASE_URL = "<External Database URL from Render dashboard>"
    python -m scripts.seed_production

Optionally pass your own product list:
    python -m scripts.seed_production "iphone 15" "tecno spark" "rice cooker"
"""
from __future__ import annotations

import sys

DEFAULT_PRODUCTS = [
    "samsung galaxy a15", "tecno spark 20", "infinix hot 40", "iphone 13",
    "redmi note 13", "hp laptop", "dell laptop", "lenovo ideapad",
    "bluetooth speaker", "power bank 20000mah", "smart tv 43 inch",
    "rice cooker", "air fryer", "blender", "standing fan",
    "office chair", "gas cooker", "washing machine", "jbl earbuds",
    "ps5 controller",
]


def main() -> None:
    products = sys.argv[1:] or DEFAULT_PRODUCTS

    import os

    url = os.getenv("DATABASE_URL", "")
    if "sqlite" in url or not url:
        print("DATABASE_URL is not set to a production database.")
        print("Set it to Render's External Database URL first, e.g.:")
        print('  $env:DATABASE_URL = "postgresql://user:pass@host/db"')
        raise SystemExit(1)

    from app.core.search_service import execute_search
    from app.db.session import db_session, init_db
    from app.schemas.api import SearchRequest

    init_db()
    ok = failed = 0
    with db_session() as db:
        for i, name in enumerate(products, 1):
            try:
                result = execute_search(
                    db,
                    SearchRequest(query=name, use_cache=False,
                                  retailers=["jumia", "konga"]),
                )
                live = sum(1 for s in result.sources_status if s.ok)
                print(f"[{i:>2}/{len(products)}] {name:<28} "
                      f"{len(result.listings):>3} listings "
                      f"({live} sources ok)")
                ok += 1
            except Exception as exc:  # noqa: BLE001 — keep seeding
                print(f"[{i:>2}/{len(products)}] {name:<28} FAILED: {exc}")
                failed += 1
    print(f"\ndone: {ok} seeded, {failed} failed.")
    print("Production picks up the new products on its next restart "
          "(Render dashboard → Manual Deploy → Deploy latest commit, "
          "or just wait for your next git push).")


if __name__ == "__main__":
    main()
