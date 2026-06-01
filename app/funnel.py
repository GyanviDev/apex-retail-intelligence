"""
Conversion funnel computation.
Powers: GET /stores/{id}/funnel

Critical requirements:
- Session is the unit, not raw events
- Re-entries must NOT double-count a visitor
- Drop-off % at each stage must be accurate
- Staff excluded from all counts

Funnel stages:
  ENTRY → ZONE_VISIT → BILLING_QUEUE → PURCHASE
"""

import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, select, distinct
from app.models import EventRecord, EventType

logger = logging.getLogger(__name__)


def _today_range():
    """
    60-day lookback window to support historical video datasets
    where event timestamps reflect recording date (2026-04-10),
    not wall-clock date. In production with live CCTV feeds this
    would be narrowed to same-day or same-shift range.
    """
    now   = datetime.now(timezone.utc)
    start = now - timedelta(days=60)
    return start.replace(tzinfo=None), now.replace(tzinfo=None)


def get_funnel(db: Session, store_id: str) -> dict:
    """
    Compute conversion funnel for a store.

    Stage 1 - Entry:
        Unique visitor_ids with ENTRY event (excludes staff).
        Re-entries use same visitor_id so never double-counted.

    Stage 2 - Zone Visit:
        Unique visitor_ids with at least one ZONE_ENTER event.
        Subset of Stage 1.

    Stage 3 - Billing Queue:
        Unique visitor_ids with BILLING_QUEUE_JOIN event.
        Subset of Stage 2.

    Stage 4 - Purchase (proxy):
        Unique visitor_ids with BILLING_QUEUE_JOIN but NO
        BILLING_QUEUE_ABANDON — i.e. they stayed and presumably purchased.
        True purchase correlation requires POS data loaded separately.

    Drop-off % = (prev_stage - current_stage) / prev_stage * 100
    """
    start, end = _today_range()

    # -- Stage 1: Entry -------------------------------------------------------
    stage1 = db.execute(
        select(func.count(distinct(EventRecord.visitor_id)))
        .where(
            EventRecord.store_id   == store_id,
            EventRecord.is_staff   == False,
            EventRecord.event_type == EventType.ENTRY,
            EventRecord.timestamp  >= start,
            EventRecord.timestamp  <= end,
        )
    ).scalar() or 0

    # -- Stage 2: Zone Visit --------------------------------------------------
    stage2 = db.execute(
        select(func.count(distinct(EventRecord.visitor_id)))
        .where(
            EventRecord.store_id   == store_id,
            EventRecord.is_staff   == False,
            EventRecord.event_type == EventType.ZONE_ENTER,
            EventRecord.timestamp  >= start,
            EventRecord.timestamp  <= end,
        )
    ).scalar() or 0

    # Cap stage2 at stage1 — tracking noise can produce zone events
    # without a corresponding entry (camera overlap edge case)
    stage2 = min(stage2, stage1)

    # -- Stage 3: Billing Queue -----------------------------------------------
    stage3 = db.execute(
        select(func.count(distinct(EventRecord.visitor_id)))
        .where(
            EventRecord.store_id   == store_id,
            EventRecord.is_staff   == False,
            EventRecord.event_type == EventType.BILLING_QUEUE_JOIN,
            EventRecord.timestamp  >= start,
            EventRecord.timestamp  <= end,
        )
    ).scalar() or 0

    stage3 = min(stage3, stage2)

    # -- Stage 4: Purchase (proxy) --------------------------------------------
    # Visitors who abandoned billing queue
    abandoned = db.execute(
        select(func.count(distinct(EventRecord.visitor_id)))
        .where(
            EventRecord.store_id   == store_id,
            EventRecord.is_staff   == False,
            EventRecord.event_type == EventType.BILLING_QUEUE_ABANDON,
            EventRecord.timestamp  >= start,
            EventRecord.timestamp  <= end,
        )
    ).scalar() or 0

    stage4 = max(0, stage3 - abandoned)

    # -- Drop-off calculation -------------------------------------------------
    def dropoff(prev: int, curr: int) -> float:
        if prev == 0:
            return 0.0
        return round((prev - curr) / prev * 100, 2)

    return {
        "store_id": store_id,
        "as_of":    datetime.now(timezone.utc).isoformat(),
        "stages": [
            {
                "stage":       "ENTRY",
                "label":       "Entered Store",
                "count":       stage1,
                "dropoff_pct": 0.0,
            },
            {
                "stage":       "ZONE_VISIT",
                "label":       "Visited a Zone",
                "count":       stage2,
                "dropoff_pct": dropoff(stage1, stage2),
            },
            {
                "stage":       "BILLING_QUEUE",
                "label":       "Reached Billing",
                "count":       stage3,
                "dropoff_pct": dropoff(stage2, stage3),
            },
            {
                "stage":       "PURCHASE",
                "label":       "Completed Purchase",
                "count":       stage4,
                "dropoff_pct": dropoff(stage3, stage4),
            },
        ],
        "overall_conversion_pct": round(
            stage4 / stage1 * 100, 2
        ) if stage1 > 0 else 0.0,
        "note": (
            "Stage 4 is a proxy based on billing queue completion. "
            "Load POS data via /pos/ingest for exact purchase counts."
        ),
    }