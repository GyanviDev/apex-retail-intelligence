"""
Anomaly detection engine.
Powers: GET /stores/{id}/anomalies

Required anomaly types per spec:
- BILLING_QUEUE_SPIKE: queue depth exceeds threshold
- CONVERSION_DROP: conversion rate vs 7-day average
- DEAD_ZONE: no visits in 30 minutes

Severity levels: INFO / WARN / CRITICAL
Each anomaly includes suggested_action string.
"""

import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, select, distinct
from app.models import EventRecord, EventType, AnomalySeverity

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
QUEUE_SPIKE_WARN     = 5   # visitors in billing zone
QUEUE_SPIKE_CRITICAL = 10
CONVERSION_DROP_WARN = 0.20    # 20% drop vs 7-day avg
CONVERSION_DROP_CRIT = 0.40    # 40% drop vs 7-day avg
DEAD_ZONE_MINUTES    = 30


def _now_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _detect_queue_spike(db: Session, store_id: str) -> list[dict]:
    """
    Detect billing queue buildup anomaly.
    Counts visitors who joined billing queue in last 15 minutes
    and have not exited.
    """
    anomalies = []
    now   = _now_naive()
    start = now - timedelta(minutes=15)

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

    for row in joined:
        depth    = row.count
        zone_id  = row.zone_id

        if depth >= QUEUE_SPIKE_CRITICAL:
            severity = AnomalySeverity.CRITICAL
            action   = (
                f"Queue depth {depth} at {zone_id}. "
                "Open additional billing counter immediately."
            )
        elif depth >= QUEUE_SPIKE_WARN:
            severity = AnomalySeverity.WARN
            action   = (
                f"Queue depth {depth} at {zone_id}. "
                "Consider opening an additional counter."
            )
        else:
            continue

        anomalies.append({
            "anomaly_type":     "BILLING_QUEUE_SPIKE",
            "severity":         severity,
            "zone_id":          zone_id,
            "value":            depth,
            "threshold":        QUEUE_SPIKE_WARN,
            "detected_at":      datetime.now(timezone.utc).isoformat(),
            "suggested_action": action,
        })

    return anomalies


def _detect_conversion_drop(db: Session, store_id: str) -> list[dict]:
    """
    Detect conversion rate drop vs 7-day average.
    Compares today's conversion rate against rolling 7-day baseline.
    """
    anomalies = []
    now   = _now_naive()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start  = today_start - timedelta(days=7)

    # Today's visitors and billing reach
    today_visitors = db.execute(
        select(func.count(distinct(EventRecord.visitor_id)))
        .where(
            EventRecord.store_id   == store_id,
            EventRecord.is_staff   == False,
            EventRecord.event_type == EventType.ENTRY,
            EventRecord.timestamp  >= today_start,
        )
    ).scalar() or 0

    today_converted = db.execute(
        select(func.count(distinct(EventRecord.visitor_id)))
        .where(
            EventRecord.store_id   == store_id,
            EventRecord.is_staff   == False,
            EventRecord.event_type == EventType.BILLING_QUEUE_JOIN,
            EventRecord.timestamp  >= today_start,
        )
    ).scalar() or 0

    # 7-day baseline
    week_visitors = db.execute(
        select(func.count(distinct(EventRecord.visitor_id)))
        .where(
            EventRecord.store_id   == store_id,
            EventRecord.is_staff   == False,
            EventRecord.event_type == EventType.ENTRY,
            EventRecord.timestamp  >= week_start,
            EventRecord.timestamp  <  today_start,
        )
    ).scalar() or 0

    week_converted = db.execute(
        select(func.count(distinct(EventRecord.visitor_id)))
        .where(
            EventRecord.store_id   == store_id,
            EventRecord.is_staff   == False,
            EventRecord.event_type == EventType.BILLING_QUEUE_JOIN,
            EventRecord.timestamp  >= week_start,
            EventRecord.timestamp  <  today_start,
        )
    ).scalar() or 0

    if today_visitors < 10 or week_visitors < 10:
        # Insufficient data — emit INFO not WARN
        if today_visitors > 0:
            anomalies.append({
                "anomaly_type":     "CONVERSION_DROP",
                "severity":         AnomalySeverity.INFO,
                "value":            None,
                "threshold":        None,
                "detected_at":      datetime.now(timezone.utc).isoformat(),
                "suggested_action": (
                    "Insufficient historical data for conversion baseline. "
                    "Anomaly detection active after 10+ daily sessions."
                ),
            })
        return anomalies

    today_rate = today_converted / today_visitors
    week_rate  = week_converted  / week_visitors

    if week_rate == 0:
        return anomalies

    drop = (week_rate - today_rate) / week_rate

    if drop >= CONVERSION_DROP_CRIT:
        severity = AnomalySeverity.CRITICAL
        action   = (
            f"Conversion rate dropped {drop*100:.1f}% vs 7-day avg "
            f"({today_rate*100:.1f}% vs {week_rate*100:.1f}%). "
            "Immediate store manager review required."
        )
    elif drop >= CONVERSION_DROP_WARN:
        severity = AnomalySeverity.WARN
        action   = (
            f"Conversion rate dropped {drop*100:.1f}% vs 7-day avg. "
            "Review product placement and staff availability."
        )
    else:
        return anomalies

    anomalies.append({
        "anomaly_type":     "CONVERSION_DROP",
        "severity":         severity,
        "today_rate":       round(today_rate, 4),
        "baseline_rate":    round(week_rate, 4),
        "drop_pct":         round(drop * 100, 2),
        "detected_at":      datetime.now(timezone.utc).isoformat(),
        "suggested_action": action,
    })

    return anomalies


def _detect_dead_zones(db: Session, store_id: str) -> list[dict]:
    """
    Detect zones with no visits in the last DEAD_ZONE_MINUTES.
    Only flags zones that had visits earlier today —
    avoids false alerts for zones that simply don't exist.
    """
    anomalies = []
    now         = _now_naive()
    cutoff      = now - timedelta(minutes=DEAD_ZONE_MINUTES)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Zones active today
    active_zones = db.execute(
        select(distinct(EventRecord.zone_id))
        .where(
            EventRecord.store_id   == store_id,
            EventRecord.is_staff   == False,
            EventRecord.zone_id    != None,
            EventRecord.timestamp  >= today_start,
        )
    ).scalars().all()

    for zone_id in active_zones:
        # Last visit in this zone
        last_visit = db.execute(
            select(func.max(EventRecord.timestamp))
            .where(
                EventRecord.store_id   == store_id,
                EventRecord.is_staff   == False,
                EventRecord.zone_id    == zone_id,
                EventRecord.event_type == EventType.ZONE_ENTER,
            )
        ).scalar()

        if last_visit and last_visit < cutoff:
            minutes_silent = int((now - last_visit).total_seconds() / 60)
            anomalies.append({
                "anomaly_type":     "DEAD_ZONE",
                "severity":         AnomalySeverity.WARN,
                "zone_id":          zone_id,
                "minutes_silent":   minutes_silent,
                "last_visit_at":    last_visit.isoformat(),
                "detected_at":      datetime.now(timezone.utc).isoformat(),
                "suggested_action": (
                    f"Zone {zone_id} has had no customer visits "
                    f"for {minutes_silent} minutes. "
                    "Check for display issues or poor signage."
                ),
            })

    return anomalies


def get_anomalies(db: Session, store_id: str) -> dict:
    """
    Run all anomaly detectors and return combined result.
    Always returns valid structure even with zero anomalies.
    """
    anomalies = []
    anomalies.extend(_detect_queue_spike(db, store_id))
    anomalies.extend(_detect_conversion_drop(db, store_id))
    anomalies.extend(_detect_dead_zones(db, store_id))

    # Sort by severity: CRITICAL first
    severity_order = {
        AnomalySeverity.CRITICAL: 0,
        AnomalySeverity.WARN:     1,
        AnomalySeverity.INFO:     2,
    }
    anomalies.sort(key=lambda x: severity_order.get(x["severity"], 3))

    return {
        "store_id":      store_id,
        "as_of":         datetime.now(timezone.utc).isoformat(),
        "anomaly_count": len(anomalies),
        "anomalies":     anomalies,
    }