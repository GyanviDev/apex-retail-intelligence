"""
Real-time metric computation.
Powers: GET /stores/{id}/metrics
        GET /stores/{id}/heatmap

Critical requirements:
- Exclude is_staff=true from ALL customer metrics
- Handle zero-traffic periods without null returns
- Real-time — queries live DB, not cached snapshots
- data_confidence flag when fewer than 20 sessions
"""

import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, select, distinct, case, and_
from app.models import EventRecord, EventType

logger = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _today_range():
    """
    Return (start, end) time window for metric queries.
    Uses a 60-day lookback window to support historical video datasets
    where event timestamps reflect recording date (2026-04-10),
    not wall-clock date. In production with live CCTV feeds this
    window would be narrowed to same-day or same-shift range.
    """
    now   = datetime.now(timezone.utc)
    start = now - timedelta(days=60)
    return start.replace(tzinfo=None), now.replace(tzinfo=None)


def _customer_base_query(db: Session, store_id: str):
    """Base filter: this store, lookback window, not staff."""
    start, end = _today_range()
    return db.query(EventRecord).filter(
        EventRecord.store_id  == store_id,
        EventRecord.is_staff  == False,
        EventRecord.timestamp >= start,
        EventRecord.timestamp <= end,
    )


# ── Unique Visitors ───────────────────────────────────────────────────────────

def get_unique_visitors(db: Session, store_id: str) -> int:
    """
    Count unique customer visitor_ids with at least one ENTRY event.
    Re-entries use the same visitor_id so they are NOT double-counted.
    """
    start, end = _today_range()
    result = db.execute(
        select(func.count(distinct(EventRecord.visitor_id)))
        .where(
            EventRecord.store_id   == store_id,
            EventRecord.is_staff   == False,
            EventRecord.event_type == EventType.ENTRY,
            EventRecord.timestamp  >= start,
            EventRecord.timestamp  <= end,
        )
    ).scalar()
    return result or 0


# ── Conversion Rate ───────────────────────────────────────────────────────────

def get_conversion_rate(db: Session, store_id: str,
                        pos_df=None) -> dict:
    """
    Conversion = visitors who had a BILLING_QUEUE_JOIN
    divided by total unique visitors.

    Uses BILLING_QUEUE_JOIN as proxy for purchase intent.
    Returns dict with rate, converted_count, total_visitors.
    """
    total = get_unique_visitors(db, store_id)

    if total == 0:
        return {
            "rate":            0.0,
            "converted_count": 0,
            "total_visitors":  0,
        }

    start, end = _today_range()

    converted = db.execute(
        select(func.count(distinct(EventRecord.visitor_id)))
        .where(
            EventRecord.store_id   == store_id,
            EventRecord.is_staff   == False,
            EventRecord.event_type.in_([EventType.BILLING_QUEUE_JOIN]),
            EventRecord.timestamp  >= start,
            EventRecord.timestamp  <= end,
        )
    ).scalar() or 0

    rate = round(converted / total, 4) if total > 0 else 0.0
    return {
        "rate":            rate,
        "converted_count": converted,
        "total_visitors":  total,
    }


# ── Average Dwell Per Zone ────────────────────────────────────────────────────

def get_avg_dwell_per_zone(db: Session, store_id: str) -> dict:
    """
    Compute average dwell_ms per zone from ZONE_DWELL events.
    Excludes staff. Returns {zone_id: avg_dwell_ms}.
    Returns empty dict for zero-traffic periods — never null.
    """
    start, end = _today_range()

    rows = db.execute(
        select(
            EventRecord.zone_id,
            func.avg(EventRecord.dwell_ms).label("avg_dwell"),
            func.count(EventRecord.event_id).label("sample_count"),
        )
        .where(
            EventRecord.store_id   == store_id,
            EventRecord.is_staff   == False,
            EventRecord.event_type == EventType.ZONE_DWELL,
            EventRecord.zone_id    != None,
            EventRecord.timestamp  >= start,
            EventRecord.timestamp  <= end,
        )
        .group_by(EventRecord.zone_id)
    ).fetchall()

    return {
        row.zone_id: {
            "avg_dwell_ms": round(row.avg_dwell or 0, 2),
            "sample_count": row.sample_count,
        }
        for row in rows
    }


# ── Queue Depth ───────────────────────────────────────────────────────────────

def get_current_queue_depth(db: Session, store_id: str) -> dict:
    """
    Current billing queue depth per zone.
    Computed as: visitors who joined queue but have not exited billing zone.
    Uses full lookback window to capture historical dataset queue state.
    """
    start, end = _today_range()

    joined = db.execute(
        select(
            EventRecord.zone_id,
            func.count(distinct(EventRecord.visitor_id)).label("count")
        )
        .where(
            EventRecord.store_id   == store_id,
            EventRecord.is_staff   == False,
            EventRecord.event_type == EventType.BILLING_QUEUE_JOIN,
            EventRecord.timestamp  >= start,
        )
        .group_by(EventRecord.zone_id)
    ).fetchall()

    exited = db.execute(
        select(
            EventRecord.zone_id,
            func.count(distinct(EventRecord.visitor_id)).label("count")
        )
        .where(
            EventRecord.store_id   == store_id,
            EventRecord.is_staff   == False,
            EventRecord.event_type == EventType.ZONE_EXIT,
            EventRecord.timestamp  >= start,
        )
        .group_by(EventRecord.zone_id)
    ).fetchall()

    joined_map = {r.zone_id: r.count for r in joined}
    exited_map = {r.zone_id: r.count for r in exited}

    result = {}
    for zone_id in joined_map:
        depth = max(0, joined_map[zone_id] - exited_map.get(zone_id, 0))
        result[zone_id] = depth

    return result or {}


# ── Abandonment Rate ──────────────────────────────────────────────────────────

def get_abandonment_rate(db: Session, store_id: str) -> dict:
    """
    Abandonment = BILLING_QUEUE_ABANDON / BILLING_QUEUE_JOIN.
    Returns rate and raw counts.
    """
    start, end = _today_range()

    joins = db.execute(
        select(func.count(EventRecord.event_id))
        .where(
            EventRecord.store_id   == store_id,
            EventRecord.is_staff   == False,
            EventRecord.event_type == EventType.BILLING_QUEUE_JOIN,
            EventRecord.timestamp  >= start,
            EventRecord.timestamp  <= end,
        )
    ).scalar() or 0

    abandons = db.execute(
        select(func.count(EventRecord.event_id))
        .where(
            EventRecord.store_id   == store_id,
            EventRecord.is_staff   == False,
            EventRecord.event_type == EventType.BILLING_QUEUE_ABANDON,
            EventRecord.timestamp  >= start,
            EventRecord.timestamp  <= end,
        )
    ).scalar() or 0

    rate = round(abandons / joins, 4) if joins > 0 else 0.0
    return {
        "rate":          rate,
        "abandon_count": abandons,
        "join_count":    joins,
    }


# ── Heatmap ───────────────────────────────────────────────────────────────────

def get_heatmap(db: Session, store_id: str) -> dict:
    """
    Zone visit frequency + avg dwell, normalised 0-100.
    Includes data_confidence flag if fewer than 20 sessions.
    """
    start, end     = _today_range()
    total_visitors = get_unique_visitors(db, store_id)
    low_confidence = total_visitors < 20

    rows = db.execute(
        select(
            EventRecord.zone_id,
            func.count(distinct(EventRecord.visitor_id)).label("visit_count"),
            func.avg(EventRecord.dwell_ms).label("avg_dwell"),
        )
        .where(
            EventRecord.store_id   == store_id,
            EventRecord.is_staff   == False,
            EventRecord.event_type == EventType.ZONE_ENTER,
            EventRecord.zone_id    != None,
            EventRecord.timestamp  >= start,
            EventRecord.timestamp  <= end,
        )
        .group_by(EventRecord.zone_id)
    ).fetchall()

    if not rows:
        return {
            "zones":           {},
            "data_confidence": "LOW" if low_confidence else "OK",
        }

    max_visits = max(r.visit_count for r in rows) or 1

    zones = {}
    for row in rows:
        normalised = round((row.visit_count / max_visits) * 100, 1)
        zones[row.zone_id] = {
            "visit_count":      row.visit_count,
            "avg_dwell_ms":     round(row.avg_dwell or 0, 2),
            "normalised_score": normalised,
        }

    return {
        "zones":           zones,
        "data_confidence": "LOW" if low_confidence else "OK",
    }


# ── Master Metrics ────────────────────────────────────────────────────────────

def get_store_metrics(db: Session, store_id: str) -> dict:
    """
    Aggregate all metrics for GET /stores/{id}/metrics.
    Never returns null — every field has a safe zero default.
    """
    return {
        "store_id":           store_id,
        "as_of":              datetime.now(timezone.utc).isoformat(),
        "unique_visitors":    get_unique_visitors(db, store_id),
        "conversion":         get_conversion_rate(db, store_id),
        "avg_dwell_per_zone": get_avg_dwell_per_zone(db, store_id),
        "queue_depth":        get_current_queue_depth(db, store_id),
        "abandonment":        get_abandonment_rate(db, store_id),
    }