"""
Event ingestion layer.
Handles: validation, deduplication, partial success, structured errors.

Critical requirements from spec:
- Idempotent by event_id (safe to call twice with same payload)
- Partial success on malformed events (don't reject entire batch)
- Max batch size: 500
- Structured error response per rejected event
"""

import logging
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.models import (
    StoreEvent,
    EventBatch,
    EventRecord,
    IngestResponse,
    EventType,
)

logger = logging.getLogger(__name__)


def event_to_record(event: StoreEvent) -> EventRecord:
    """Convert a validated Pydantic StoreEvent to a SQLAlchemy EventRecord."""
    return EventRecord(
        event_id   = event.event_id,
        store_id   = event.store_id,
        camera_id  = event.camera_id,
        visitor_id = event.visitor_id,
        event_type = event.event_type,
        timestamp  = event.timestamp.replace(tzinfo=None),  # SQLite stores naive
        zone_id    = event.zone_id,
        dwell_ms   = event.dwell_ms,
        is_staff   = event.is_staff,
        confidence = event.confidence,
        meta_json  = {
            "queue_depth": event.metadata.queue_depth,
            "sku_zone":    event.metadata.sku_zone,
            "session_seq": event.metadata.session_seq,
        },
    )


def check_duplicate(db: Session, event_id: str) -> bool:
    """Return True if event_id already exists in the database."""
    result = db.execute(
        select(EventRecord.event_id).where(EventRecord.event_id == event_id)
    ).first()
    return result is not None


def ingest_batch(
    raw_events: list[dict],
    db: Session,
) -> IngestResponse:
    """
    Ingest a batch of raw event dicts.

    Strategy:
    - Validate each event individually against StoreEvent schema
    - Check for duplicates by event_id
    - Insert accepted events in a single DB transaction
    - Return structured counts and per-event errors

    This is intentionally NOT an all-or-nothing transaction.
    Partial success is required by the spec.
    """
    accepted  = 0
    rejected  = 0
    duplicate = 0
    errors    = []
    records   = []

    for idx, raw in enumerate(raw_events):
        event_id = raw.get("event_id", f"unknown_{idx}")

        # ── Step 1: Schema validation ─────────────────────────────────────
        try:
            event = StoreEvent.model_validate(raw)
        except Exception as e:
            rejected += 1
            errors.append({
                "index":    idx,
                "event_id": event_id,
                "reason":   "SCHEMA_INVALID",
                "detail":   str(e)[:300],  # truncate for response size
            })
            logger.warning(f"Schema validation failed for event {event_id}: {e}")
            continue

        # ── Step 2: Deduplication ─────────────────────────────────────────
        if check_duplicate(db, event.event_id):
            duplicate += 1
            logger.debug(f"Duplicate event skipped: {event.event_id}")
            continue

        # ── Step 3: Queue for batch insert ────────────────────────────────
        records.append(event_to_record(event))
        accepted += 1

    # ── Step 4: Batch insert ──────────────────────────────────────────────
    if records:
        try:
            db.bulk_save_objects(records)
            db.commit()
            logger.info(
                f"Ingested batch: accepted={accepted} "
                f"rejected={rejected} duplicate={duplicate}"
            )
        except Exception as e:
            db.rollback()
            logger.error(f"Batch insert failed: {e}")
            # Move all accepted to rejected on DB failure
            rejected  += accepted
            accepted   = 0
            errors.append({
                "index":    -1,
                "event_id": "BATCH",
                "reason":   "DB_INSERT_FAILED",
                "detail":   str(e)[:300],
            })

    return IngestResponse(
        accepted  = accepted,
        rejected  = rejected,
        duplicate = duplicate,
        errors    = errors,
    )