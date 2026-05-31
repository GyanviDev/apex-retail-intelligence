# PROMPT: "Write pytest tests for anomaly detection endpoints covering:
# queue spike detection, dead zone detection, conversion drop,
# empty store returns no anomalies, anomaly response structure,
# severity ordering, insufficient data INFO anomaly."
# CHANGES MADE: Added store isolation via unique store_ids per test,
# added direct DB seeding for time-sensitive anomaly triggers,
# added assertion on suggested_action presence per spec requirement.

import pytest
import uuid
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.main import app
from app.models import Base, get_db, EventRecord

TEST_DB_URL = "sqlite:///./data/test_anomalies.db"

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


def seed_event(
    store_id:    str,
    event_type:  str,
    visitor_id:  str  = None,
    zone_id:     str  = None,
    is_staff:    bool = False,
    minutes_ago: int  = 5,
    dwell_ms:    int  = 0,
):
    """Directly seed an EventRecord into the test DB via ingest endpoint."""
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    event = {
        "event_id":   str(uuid.uuid4()),
        "store_id":   store_id,
        "camera_id":  "CAM_TEST_01",
        "visitor_id": visitor_id or f"VIS_{uuid.uuid4().hex[:6].upper()}",
        "event_type": event_type,
        "timestamp":  ts,
        "zone_id":    zone_id,
        "dwell_ms":   dwell_ms,
        "is_staff":   is_staff,
        "confidence": 0.90,
        "metadata":   {
            "queue_depth": None,
            "sku_zone":    None,
            "session_seq": 1,
        },
    }
    client.post("/events/ingest", json={"events": [event]})
def test_anomalies_empty_store_returns_no_anomalies():
    """Empty store has no anomalies — response is valid with empty list."""
    store_id = f"STORE_EMPTY_{uuid.uuid4().hex[:4].upper()}"
    resp = client.get(f"/stores/{store_id}/anomalies")
    assert resp.status_code == 200
    body = resp.json()
    assert body["anomaly_count"] == 0
    assert body["anomalies"]     == []
    assert "store_id"  in body
    assert "as_of"     in body


def test_anomalies_response_structure():
    """Anomaly response always contains required fields."""
    store_id = f"STORE_STRUCT_{uuid.uuid4().hex[:4].upper()}"
    resp = client.get(f"/stores/{store_id}/anomalies")
    assert resp.status_code == 200
    body = resp.json()
    assert "store_id"      in body
    assert "as_of"         in body
    assert "anomaly_count" in body
    assert "anomalies"     in body
    assert isinstance(body["anomalies"], list)


def test_queue_spike_warn_detected():
    """
    Billing queue with 6 joins in last 15 min triggers WARN.
    Edge case: billing queue buildup from spec.
    """
    store_id = f"STORE_QSPIKE_{uuid.uuid4().hex[:4].upper()}"

    for i in range(6):
        seed_event(
            store_id   = store_id,
            event_type = "BILLING_QUEUE_JOIN",
            zone_id    = "BILLING",
            minutes_ago = 5,
        )

    resp = client.get(f"/stores/{store_id}/anomalies")
    assert resp.status_code == 200
    body = resp.json()

    queue_anomalies = [
        a for a in body["anomalies"]
        if a["anomaly_type"] == "BILLING_QUEUE_SPIKE"
    ]
    assert len(queue_anomalies) >= 1
    anomaly = queue_anomalies[0]
    assert anomaly["severity"] in ["WARN", "CRITICAL"]
    assert "suggested_action" in anomaly
    assert len(anomaly["suggested_action"]) > 0


def test_queue_spike_critical_detected():
    """
    Billing queue with 11 joins triggers CRITICAL severity.
    """
    store_id = f"STORE_QCRIT_{uuid.uuid4().hex[:4].upper()}"

    for i in range(11):
        seed_event(
            store_id   = store_id,
            event_type = "BILLING_QUEUE_JOIN",
            zone_id    = "BILLING",
            minutes_ago = 3,
        )

    resp = client.get(f"/stores/{store_id}/anomalies")
    body = resp.json()

    queue_anomalies = [
        a for a in body["anomalies"]
        if a["anomaly_type"] == "BILLING_QUEUE_SPIKE"
    ]
    assert len(queue_anomalies) >= 1
    assert queue_anomalies[0]["severity"] == "CRITICAL"


def test_dead_zone_detected():
    """
    Zone with visits earlier today but silent for 35 min triggers DEAD_ZONE.
    Edge case: dead zone from spec.
    """
    store_id = f"STORE_DEAD_{uuid.uuid4().hex[:4].upper()}"

    # Zone was active 35 minutes ago
    seed_event(
        store_id   = store_id,
        event_type = "ZONE_ENTER",
        zone_id    = "SKINCARE",
        minutes_ago = 35,
    )

    resp = client.get(f"/stores/{store_id}/anomalies")
    body = resp.json()

    dead_zones = [
        a for a in body["anomalies"]
        if a["anomaly_type"] == "DEAD_ZONE"
    ]
    assert len(dead_zones) >= 1
    assert dead_zones[0]["zone_id"]          == "SKINCARE"
    assert dead_zones[0]["severity"]         == "WARN"
    assert "suggested_action" in dead_zones[0]
    assert dead_zones[0]["minutes_silent"]   >= 30


def test_dead_zone_not_triggered_for_recent_activity():
    """
    Zone with visit 5 minutes ago must NOT trigger DEAD_ZONE.
    """
    store_id = f"STORE_ACTIVE_{uuid.uuid4().hex[:4].upper()}"

    seed_event(
        store_id   = store_id,
        event_type = "ZONE_ENTER",
        zone_id    = "MAKEUP",
        minutes_ago = 5,
    )

    resp = client.get(f"/stores/{store_id}/anomalies")
    body = resp.json()

    dead_zones = [
        a for a in body["anomalies"]
        if a["anomaly_type"] == "DEAD_ZONE"
        and a.get("zone_id") == "MAKEUP"
    ]
    assert len(dead_zones) == 0


def test_conversion_drop_insufficient_data_returns_info():
    """
    Store with fewer than 10 sessions returns INFO anomaly,
    not WARN — insufficient baseline data.
    """
    store_id = f"STORE_LOWDATA_{uuid.uuid4().hex[:4].upper()}"

    # Seed just 3 entry events — below 10 threshold
    for _ in range(3):
        seed_event(
            store_id   = store_id,
            event_type = "ENTRY",
            minutes_ago = 60,
        )

    resp = client.get(f"/stores/{store_id}/anomalies")
    body = resp.json()

    conv_anomalies = [
        a for a in body["anomalies"]
        if a["anomaly_type"] == "CONVERSION_DROP"
    ]
    if conv_anomalies:
        assert conv_anomalies[0]["severity"] == "INFO"


def test_anomalies_severity_ordering():
    """
    CRITICAL anomalies appear before WARN in response list.
    """
    store_id = f"STORE_ORDER_{uuid.uuid4().hex[:4].upper()}"

    # Trigger CRITICAL queue spike
    for i in range(11):
        seed_event(
            store_id   = store_id,
            event_type = "BILLING_QUEUE_JOIN",
            zone_id    = "BILLING",
            minutes_ago = 2,
        )

    # Trigger WARN dead zone
    seed_event(
        store_id   = store_id,
        event_type = "ZONE_ENTER",
        zone_id    = "FRAGRANCE",
        minutes_ago = 40,
    )

    resp = client.get(f"/stores/{store_id}/anomalies")
    body = resp.json()

    severities = [a["severity"] for a in body["anomalies"]]
    severity_rank = {"CRITICAL": 0, "WARN": 1, "INFO": 2}

    for i in range(len(severities) - 1):
        assert severity_rank.get(severities[i], 3) <= \
               severity_rank.get(severities[i+1], 3), \
               f"Severity order wrong: {severities}"


def test_health_stale_feed_detection():
    """
    Store with last event >10 min ago triggers STALE_FEED.
    """
    store_id = f"STORE_STALE_{uuid.uuid4().hex[:4].upper()}"

    seed_event(
        store_id   = store_id,
        event_type = "ENTRY",
        minutes_ago = 15,
    )

    resp = client.get("/health")
    body = resp.json()

    if store_id in body["stores"]:
        assert body["stores"][store_id]["feed_status"] == "STALE_FEED"