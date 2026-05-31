"""
Event emitter — converts raw tracker output into validated StoreEvent objects.
This is the contract boundary between the CV world and the business logic world.
Every event passes through here before being written to disk or sent to the API.
"""

import uuid
import json
import os
from datetime import datetime, timezone
from typing import Optional
from app.models import StoreEvent, EventType, EventMetadata


class EventEmitter:
    """
    Stateful event emitter for a single camera session.
    Tracks session sequences per visitor to populate session_seq correctly.
    """

    def __init__(self, store_id: str, camera_id: str, output_path: str):
        self.store_id    = store_id
        self.camera_id   = camera_id
        self.output_path = output_path
        self._session_seq: dict[str, int] = {}  # visitor_id -> current seq
        self._file_handle = None
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

    def open(self):
        """Open the output .jsonl file for writing."""
        self._file_handle = open(self.output_path, "a", encoding="utf-8")
        return self

    def close(self):
        """Flush and close the output file."""
        if self._file_handle:
            self._file_handle.flush()
            self._file_handle.close()

    def __enter__(self):
        return self.open()

    def __exit__(self, *args):
        self.close()

    def _next_seq(self, visitor_id: str) -> int:
        """Increment and return the session sequence for a visitor."""
        self._session_seq[visitor_id] = self._session_seq.get(visitor_id, 0) + 1
        return self._session_seq[visitor_id]

    def _build_event(
        self,
        visitor_id:  str,
        event_type:  EventType,
        timestamp:   datetime,
        zone_id:     Optional[str]  = None,
        dwell_ms:    int            = 0,
        is_staff:    bool           = False,
        confidence:  float          = 1.0,
        queue_depth: Optional[int]  = None,
        sku_zone:    Optional[str]  = None,
    ) -> StoreEvent:
        seq = self._next_seq(visitor_id)
        return StoreEvent(
            event_id   = str(uuid.uuid4()),
            store_id   = self.store_id,
            camera_id  = self.camera_id,
            visitor_id = visitor_id,
            event_type = event_type,
            timestamp  = timestamp,
            zone_id    = zone_id,
            dwell_ms   = dwell_ms,
            is_staff   = is_staff,
            confidence = round(confidence, 4),
            metadata   = EventMetadata(
                queue_depth = queue_depth,
                sku_zone    = sku_zone,
                session_seq = seq,
            ),
        )

    def _write(self, event: StoreEvent):
        """Serialize and write one event to the .jsonl file."""
        line = event.model_dump_json() + "\n"
        if self._file_handle:
            self._file_handle.write(line)

    # ── Public emit methods ──────────────────────────────────────────────

    def emit_entry(self, visitor_id: str, timestamp: datetime,
                   is_staff: bool = False, confidence: float = 1.0):
        event = self._build_event(
            visitor_id, EventType.ENTRY, timestamp,
            is_staff=is_staff, confidence=confidence
        )
        self._write(event)
        return event

    def emit_exit(self, visitor_id: str, timestamp: datetime,
                  is_staff: bool = False, confidence: float = 1.0):
        event = self._build_event(
            visitor_id, EventType.EXIT, timestamp,
            is_staff=is_staff, confidence=confidence
        )
        self._write(event)
        return event

    def emit_reentry(self, visitor_id: str, timestamp: datetime,
                     confidence: float = 1.0):
        event = self._build_event(
            visitor_id, EventType.REENTRY, timestamp,
            confidence=confidence
        )
        self._write(event)
        return event

    def emit_zone_enter(self, visitor_id: str, timestamp: datetime,
                        zone_id: str, is_staff: bool = False,
                        confidence: float = 1.0, sku_zone: Optional[str] = None):
        event = self._build_event(
            visitor_id, EventType.ZONE_ENTER, timestamp,
            zone_id=zone_id, is_staff=is_staff,
            confidence=confidence, sku_zone=sku_zone
        )
        self._write(event)
        return event

    def emit_zone_exit(self, visitor_id: str, timestamp: datetime,
                       zone_id: str, is_staff: bool = False,
                       confidence: float = 1.0):
        event = self._build_event(
            visitor_id, EventType.ZONE_EXIT, timestamp,
            zone_id=zone_id, is_staff=is_staff, confidence=confidence
        )
        self._write(event)
        return event

    def emit_zone_dwell(self, visitor_id: str, timestamp: datetime,
                        zone_id: str, dwell_ms: int,
                        is_staff: bool = False, confidence: float = 1.0,
                        sku_zone: Optional[str] = None):
        event = self._build_event(
            visitor_id, EventType.ZONE_DWELL, timestamp,
            zone_id=zone_id, dwell_ms=dwell_ms,
            is_staff=is_staff, confidence=confidence, sku_zone=sku_zone
        )
        self._write(event)
        return event

    def emit_billing_queue_join(self, visitor_id: str, timestamp: datetime,
                                zone_id: str, queue_depth: int,
                                confidence: float = 1.0):
        event = self._build_event(
            visitor_id, EventType.BILLING_QUEUE_JOIN, timestamp,
            zone_id=zone_id, queue_depth=queue_depth, confidence=confidence
        )
        self._write(event)
        return event

    def emit_billing_queue_abandon(self, visitor_id: str, timestamp: datetime,
                                   zone_id: str, confidence: float = 1.0):
        event = self._build_event(
            visitor_id, EventType.BILLING_QUEUE_ABANDON, timestamp,
            zone_id=zone_id, confidence=confidence
        )
        self._write(event)
        return event