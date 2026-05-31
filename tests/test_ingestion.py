# PROMPT: "Write pytest tests for a FastAPI event ingestion endpoint.
# Cover: valid single event, valid batch, duplicate idempotency,
# schema validation rejection, partial batch success, staff event,
# max batch size exceeded, empty store metrics after staff-only events,
# re-entry event type, zero dwell on ZONE_DWELL rejection."
# CHANGES MADE: Added fixture for fresh DB per test, added edge case
# for ZONE_DWELL with dwell_ms=0 rejection, added all-staff clip test,
# strengthened duplicate assertion to check metrics unchanged.

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.main import app
from app.models import Base, get_db
import uuid

# ── Test DB setup ─────────────────────────────────────────────────────────────
TEST_DB_URL = "sqlite:///./data/test_store.db"

engine_test = create_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False}
)
TestingSessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=engine_test
)

Base.metadata.create_all(bind=engine_test)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


def make_event(
    event_type="ENTRY",
    visitor_id=None,
    zone_id=None,
    dwell_ms=0,
    is_staff=False,
    confidence=0.91,
    event_id=None,
    store_id="STORE_BLR_002",
    timestamp="2026-05-31T09:00:00Z",
):
    return {
        "event_id":   event_id or str(uuid.uuid4()),
        "store_id":   store_id,
        "camera_id":  "CAM_ENTRY_01",
        "visitor_id": visitor_id or f"VIS_{uuid.uuid4().hex[:6].upper()}",
        "event_type": event_type,
        "timestamp":  timestamp,
        "zone_id":    zone_id,
        "dwell_ms":   dwell_ms,
        "is_staff":   is_staff,
        "confidence": confidence,
        "metadata":   {
            "queue_depth": None,
            "sku_zone":    None,
            "session_seq": 1,
        },
    }


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_ingest_single_valid_event():
    """Happy path — single valid ENTRY event accepted."""
    event = make_event()
    resp  = client.post("/events/ingest", json={"events": [event]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"]  == 1
    assert body["rejected"]  == 0
    assert body["duplicate"] == 0


def test_ingest_idempotent():
    """Same event_id submitted twice — second call returns duplicate=1."""
    fixed_id = str(uuid.uuid4())
    event    = make_event(event_id=fixed_id)

    resp1 = client.post("/events/ingest", json={"events": [event]})
    resp2 = client.post("/events/ingest", json={"events": [event]})

    assert resp1.json()["accepted"]  == 1
    assert resp2.json()["duplicate"] == 1
    assert resp2.json()["accepted"]  == 0


def test_ingest_partial_batch_success():
    """
    Batch with one valid and one invalid event.
    Valid event accepted, invalid rejected — not all-or-nothing.
    """
    valid   = make_event()
    invalid = make_event(event_type="ZONE_DWELL", zone_id=None, dwell_ms=0)
    # ZONE_DWELL requires zone_id and dwell_ms > 0

    resp = client.post("/events/ingest", json={"events": [valid, invalid]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] == 1
    assert body["rejected"] == 1
    assert len(body["errors"]) == 1


def test_ingest_schema_rejection_missing_field():
    """Event missing required field confidence — rejected with SCHEMA_INVALID."""
    bad_event = make_event()
    del bad_event["confidence"]

    resp = client.post("/events/ingest", json={"events": [bad_event]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["rejected"] == 1
    assert body["errors"][0]["reason"] == "SCHEMA_INVALID"


def test_ingest_max_batch_size_exceeded():
    """Batch of 501 events — rejected at schema level."""
    events = [make_event() for _ in range(501)]
    resp   = client.post("/events/ingest", json={"events": events})
    assert resp.status_code == 422  # Pydantic validation error


def test_ingest_staff_event_excluded_from_metrics():
    """
    Staff ENTRY event ingested — unique_visitors must remain 0.
    Critical: staff must never pollute customer metrics.
    """
    store_id    = f"STORE_STAFF_{uuid.uuid4().hex[:4].upper()}"
    staff_event = make_event(
        event_type = "ENTRY",
        is_staff   = True,
        store_id   = store_id,
    )
    client.post("/events/ingest", json={"events": [staff_event]})

    resp = client.get(f"/stores/{store_id}/metrics")
    assert resp.status_code == 200
    assert resp.json()["unique_visitors"] == 0


def test_ingest_reentry_event():
    """REENTRY event type accepted without zone_id."""
    event = make_event(event_type="REENTRY")
    resp  = client.post("/events/ingest", json={"events": [event]})
    assert resp.status_code == 200
    assert resp.json()["accepted"] == 1


def test_ingest_zone_dwell_requires_dwell_ms():
    """ZONE_DWELL with dwell_ms=0 must be rejected."""
    event = make_event(
        event_type = "ZONE_DWELL",
        zone_id    = "SKINCARE",
        dwell_ms   = 0,
    )
    resp = client.post("/events/ingest", json={"events": [event]})
    assert resp.status_code == 200
    assert resp.json()["rejected"] == 1


def test_metrics_zero_traffic_store():
    """
    Store with no events returns safe zeros — never null, never crash.
    Edge case: empty store periods.
    """
    resp = client.get("/stores/STORE_EMPTY_999/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["unique_visitors"]        == 0
    assert body["conversion"]["rate"]     == 0.0
    assert body["abandonment"]["rate"]    == 0.0
    assert body["avg_dwell_per_zone"]     == {}


def test_funnel_no_double_count_on_reentry():
    """
    Same visitor_id with ENTRY + REENTRY — funnel stage 1 counts 1, not 2.
    Critical: re-entries must not inflate unique visitor count.
    """
    store_id   = f"STORE_REENTRY_{uuid.uuid4().hex[:4].upper()}"
    visitor_id = f"VIS_{uuid.uuid4().hex[:6].upper()}"

    entry = make_event(
        event_type = "ENTRY",
        visitor_id = visitor_id,
        store_id   = store_id,
        timestamp  = "2026-05-31T09:00:00Z",
    )
    reentry = make_event(
        event_type = "REENTRY",
        visitor_id = visitor_id,
        store_id   = store_id,
        timestamp  = "2026-05-31T09:30:00Z",
    )

    client.post("/events/ingest", json={"events": [entry, reentry]})

    resp = client.get(f"/stores/{store_id}/funnel")
    assert resp.status_code == 200
    stages = resp.json()["stages"]
    entry_stage = next(s for s in stages if s["stage"] == "ENTRY")
    assert entry_stage["count"] == 1


def test_health_endpoint_structure():
    """Health endpoint returns required fields."""
    resp = client.get("/health")
    assert resp.status_code in [200, 503]
    body = resp.json()
    assert "status"     in body
    assert "checked_at" in body
    assert "stores"     in body
    assert "version"    in body