"""Background refresh job (design doc §5.2.7).

Re-searches the most popular recent queries so hot products stay fresh
without user-facing latency. Run standalone:

    python -m jobs.scheduler --interval 3600
"""
from __future__ import annotations

import argparse
import logging
import time

from sqlalchemy import desc, func, select

from app.core.search_service import execute_search
from app.db.models import SearchLog
from app.db.session import db_session, init_db
from app.schemas.api import SearchRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("scheduler")


def refresh_popular(top_n: int = 10) -> int:
    """Re-run the top-N most frequent recent queries with cache bypassed."""
    with db_session() as db:
        popular = db.execute(
            select(SearchLog.query_text, func.count().label("n"))
            .group_by(SearchLog.query_text)
            .order_by(desc("n"))
            .limit(top_n)
        ).all()
        refreshed = 0
        for query_text, _count in popular:
            try:
                execute_search(db, SearchRequest(query=query_text, use_cache=False))
                refreshed += 1
            except Exception as exc:  # noqa: BLE001 — keep the loop alive
                logger.warning("refresh failed for %r: %s", query_text, exc)
        return refreshed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=3600, help="seconds between runs")
    parser.add_argument("--once", action="store_true", help="run one refresh and exit")
    args = parser.parse_args()

    init_db()
    while True:
        n = refresh_popular()
        logger.info("refreshed %d popular queries", n)
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
