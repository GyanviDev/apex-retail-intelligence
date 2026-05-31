"""
Pydantic event schema and SQLAlchemy database models.
This file is the single source of truth for the event contract.
Every pipeline emission and API response is validated against these models.
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import (
    Column, String, Boolean, Float, Integer,
    DateTime, JSON, create_engine, Index
)
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv
import os

load_dotenv()

# ─────────────────────────────────────────────
# ENUMS — locked event type catalogue
# ─────────────────────────────────────────────

class EventType(str, Enum):
    ENTRY                 = "ENTRY"
    EXIT                  = "EXIT"
    ZONE_ENTER            = "ZONE_ENTER"
    ZONE_EXIT             = "ZONE_EXIT"
    ZONE_DWELL            = "ZONE_DWELL"
    BILLING_QUEUE_JOIN    = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY               = "REENTRY"


class AnomalySeverity(str, Enum):
    INFO     = "INFO"
    WARN     = "WARN"
    CRITICAL = "CRITICAL"


# ─────────────────────────────────────────────
# PYDANTIC MODELS — validation layer
# ─────────────────────────────────────────────

class EventMetadata(BaseModel):
    queue_depth:  Optional[int]   = Field(None, ge=0)
    sku_zone:     Optional[str]   = None
    session_seq:  Optional[int]   = Field(None, ge=1)


class StoreEvent(BaseModel):
    event_id:   str = Field(default_factory=lambda: str(uuid.uuid4()))
    store_id:   str = Field(..., min_length=1)
    camera_id:  str = Field(..., min_length=1)
    visitor_id: str = Field(..., min_length=1)
    event_type: EventType
    timestamp:  datetime
    zone_id:    Optional[str]   = None
    dwell_ms:   int             = Field(default=0, ge=0)
    is_staff:   bool            = False
    confidence: float           = Field(..., ge=0.0, le=1.0)
    metadata:   EventMetadata   = Field(default_factory=EventMetadata)

    @field_validator("zone_id")
    @classmethod
    def zone_required_for_zone_events(cls, v, info):
        zone_events = {
            EventType.ZONE_ENTER,
            EventType.ZONE_EXIT,
            EventType.ZONE_DWELL,
            EventType.BILLING_QUEUE_JOIN,
            EventType.BILLING_QUEUE_ABANDON,
        }
        if info.data.get("event_type") in zone_events and v is None:
            raise ValueError("zone_id is required for zone-type events")
        return v

    @field_validator("dwell_ms")
    @classmethod
    def dwell_required_for_dwell_events(cls, v, info):
        if info.data.get("event_type") == EventType.ZONE_DWELL and v == 0:
            raise ValueError("dwell_ms must be > 0 for ZONE_DWELL events")
        return v

    model_config = ConfigDict(use_enum_values=True)


class EventBatch(BaseModel):
    events: list[StoreEvent] = Field(..., min_length=1, max_length=500)


class IngestResponse(BaseModel):
    accepted:  int
    rejected:  int
    duplicate: int
    errors:    list[dict] = []


# ─────────────────────────────────────────────
# SQLALCHEMY MODELS — persistence layer
# ─────────────────────────────────────────────

Base = declarative_base()


class EventRecord(Base):
    __tablename__ = "events"

    event_id   = Column(String,  primary_key=True)
    store_id   = Column(String,  nullable=False, index=True)
    camera_id  = Column(String,  nullable=False)
    visitor_id = Column(String,  nullable=False, index=True)
    event_type = Column(String,  nullable=False, index=True)
    timestamp  = Column(DateTime, nullable=False, index=True)
    zone_id    = Column(String,  nullable=True)
    dwell_ms   = Column(Integer, default=0)
    is_staff   = Column(Boolean, default=False)
    confidence = Column(Float,   nullable=False)
    meta_json  = Column(JSON,    nullable=True)

    __table_args__ = (
        Index("ix_store_time", "store_id", "timestamp"),
        Index("ix_visitor_store", "visitor_id", "store_id"),
    )


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/store_intelligence.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Create all tables. Safe to call multiple times."""
    os.makedirs("data", exist_ok=True)
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency — yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()# ─────────────────