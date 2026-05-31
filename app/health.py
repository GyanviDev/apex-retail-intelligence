"""
Health endpoint.
Powers: GET /health

Requirements per spec:
- Service status
- Last event timestamp per store
- STALE_FEED warning if >10 min lag
- Must be accurate — this is what an on-call engineer checks first
"""

import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, select, distinct
from app.models import EventRecord

logger = logging.getLogger(__name__)

STALE_FEED_MINUTES = 10


def _now_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_health(db: Session) -> dict:
    """
    Return service health status with per-store feed freshness.
    Never crashes — DB errors return degraded status with explanation.
    """
    now = _now_naive()

    try:
        # Get last event timestamp per store
        rows = db.execute(
            select(
                EventRecord.store_id,
                func.max(EventRecord.timestamp).label("last_event"),
                func.count(EventRecord.event_id).label("total_events"),
            )
            .group_by(EventRecord.store_id)
        ).fetchall()

        stores = {}
        overall_status = "OK"

        for row in rows:
            last_event = row.last_event
            lag_seconds = int(
                (now - last_event).total_seconds()
            ) if last_event else None

            is_stale = (
                lag_seconds is None or
                lag_seconds > STALE_FEED_MINUTES * 60
            )

            if is_stale:
                overall_status = "DEGRADED"

            stores[row.store_id] = {
                "last_event_at":  last_event.isoformat() if last_event else None,
                "lag_seconds":    lag_seconds,
                "total_events":   row.total_events,
                "feed_status":    "STALE_FEED" if is_stale else "OK",
            }

        return {
            "status":     overall_status,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "stores":     stores,
            "version":    "1.0.0",
        }

    except Exception as e:
        logger.error(f"Health check DB error: {e}")
        return {
            "status":     "DEGRADED",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "stores":     {},
            "error":      "Database unavailable",
            "version":    "1.0.0",
        }