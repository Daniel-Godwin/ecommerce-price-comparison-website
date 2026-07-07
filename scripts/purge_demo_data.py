"""One-time cleanup: remove DemoStore data from a database.

Deletes DemoStore price snapshots, listings, the retailer row, and any
products left with no listings. Run against production the same way as
the collector:

    $env:COLLECTOR_DATABASE_URL = "<External Database URL>"
    python -m scripts.purge_demo_data
"""
from __future__ import annotations

import logging
import os

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("purge_demo")


def main() -> None:
    prod_url = os.getenv("COLLECTOR_DATABASE_URL")
    if prod_url:
        os.environ["DATABASE_URL"] = prod_url

    from sqlalchemy import delete, select

    from app.db.models import ListingRow, PriceSnapshot, Product, Retailer
    from app.db.session import db_session, init_db

    init_db()
    with db_session() as db:
        retailer = db.scalar(
            select(Retailer).where(Retailer.name.like("DemoStore%"))
        )
        if retailer is None:
            logger.info("no DemoStore retailer found — nothing to purge")
            return

        listing_ids = [
            row.id for row in
            db.scalars(select(ListingRow)
                       .where(ListingRow.retailer_id == retailer.id)).all()
        ]
        snaps = db.execute(
            delete(PriceSnapshot).where(PriceSnapshot.listing_id.in_(listing_ids))
        ).rowcount if listing_ids else 0
        rows = db.execute(
            delete(ListingRow).where(ListingRow.retailer_id == retailer.id)
        ).rowcount
        db.delete(retailer)
        db.flush()

        orphans = [
            p for p in db.scalars(select(Product)).all() if not p.listings
        ]
        for p in orphans:
            db.delete(p)

        logger.info("purged: %d snapshots, %d listings, %d orphan products, "
                    "1 retailer", snaps, rows, len(orphans))
    logger.info("done — refresh the site; ticker and search are demo-free")


if __name__ == "__main__":
    main()
