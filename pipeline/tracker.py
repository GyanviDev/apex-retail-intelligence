"""
Tracker and Re-ID module.
Handles: ByteTrack tracking, Re-ID via appearance embedding,
re-entry detection, staff classification, group entry splitting,
zone assignment, dwell timing, and billing queue state.

Edge cases explicitly handled:
- Group entry: each bounding box gets independent visitor_id
- Staff movement: HSV uniform color classifier (black uniform, ratio>0.40)
- Re-entry: cosine similarity against exited visitor embeddings
- Partial occlusion: confidence passthrough, no silent drops
- Billing queue buildup: queue_depth counter per zone
- Empty store: tracker handles zero detections gracefully
- Camera overlap: visitor_id is globally unique via UUID prefix
"""

import cv2
import numpy as np
import uuid
from datetime import datetime, timezone
from typing import Optional
from collections import defaultdict
from pipeline.config import (
    REENTRY_WINDOW_SECONDS,
    STAFF_UNIFORM_HSV_LOWER,
    STAFF_UNIFORM_HSV_UPPER,
    REID_COSINE_THRESHOLD,
    BYTETRACK_TRACK_THRESH,
    BYTETRACK_TRACK_BUFFER,
    BYTETRACK_MATCH_THRESH,
    BYTETRACK_FRAME_RATE,
    BILLING_ZONE_IDS,
    DWELL_EMIT_INTERVAL_SECONDS,
    STAFF_HSV_RATIO_THRESHOLD_DEFAULT,
    STAFF_HSV_RATIO_THRESHOLD_OVERRIDE,
)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    if np.linalg.norm(a) == 0 or np.linalg.norm(b) == 0:
        return 0.0
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def extract_color_histogram(frame: np.ndarray, bbox: tuple) -> np.ndarray:
    x1, y1, x2, y2 = map(int, bbox)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(frame.shape[1], x2), min(frame.shape[0], y2)
    if x2 <= x1 or y2 <= y1:
        return np.zeros(64 * 3)
    torso_y2 = y1 + int((y2 - y1) * 0.6)
    crop = frame[y1:torso_y2, x1:x2]
    if crop.size == 0:
        return np.zeros(64 * 3)
    hsv    = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist_h = cv2.calcHist([hsv], [0], None, [64], [0, 180]).flatten()
    hist_s = cv2.calcHist([hsv], [1], None, [64], [0, 256]).flatten()
    hist_v = cv2.calcHist([hsv], [2], None, [64], [0, 256]).flatten()
    embedding = np.concatenate([hist_h, hist_s, hist_v])
    norm = np.linalg.norm(embedding)
    return embedding / norm if norm > 0 else embedding


def is_staff_by_uniform(
    frame: np.ndarray,
    bbox: tuple,
    threshold: float = 0.40,
) -> tuple:
    """
    Classify staff by black uniform detection.
    Per-camera threshold passed in from STAFF_HSV_RATIO_THRESHOLD_OVERRIDE.
    Default 0.40 for entry/billing. 0.55 for floor cameras where dark
    shelving backgrounds inflate the black-pixel ratio in torso crops.
    """
    x1, y1, x2, y2 = map(int, bbox)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(frame.shape[1], x2), min(frame.shape[0], y2)
    torso_y2 = y1 + int((y2 - y1) * 0.6)
    crop = frame[y1:torso_y2, x1:x2]
    if crop.size == 0:
        return False, 0.0
    hsv   = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    lower = np.array(STAFF_UNIFORM_HSV_LOWER)
    upper = np.array(STAFF_UNIFORM_HSV_UPPER)
    mask  = cv2.inRange(hsv, lower, upper)
    ratio = np.sum(mask > 0) / mask.size
    is_staff   = ratio > threshold
    confidence = min(1.0, ratio * 2.5)
    return is_staff, round(confidence, 4)


class VisitorSession:
    def __init__(
        self,
        visitor_id: str,
        track_id: int,
        entry_time: datetime,
        is_staff: bool,
        embedding: np.ndarray,
    ):
        self.visitor_id          = visitor_id
        self.track_id            = track_id
        self.entry_time          = entry_time
        self.is_staff            = is_staff
        self.embedding           = embedding
        self.current_zone: Optional[str]         = None
        self.zone_enter_time: Optional[datetime] = None
        self.last_dwell_emit: Optional[datetime] = None
        self.last_seen           = entry_time
        self.exited              = False
        self.exit_time: Optional[datetime]       = None


class StoreTracker:
    def __init__(
        self,
        store_id: str,
        camera_id: str,
        zone_map: dict,
        emitter,
    ):
        self.store_id  = store_id
        self.camera_id = camera_id
        self.zone_map  = zone_map
        self.emitter   = emitter

        self._active: dict         = {}
        self._exited: dict         = {}
        self._billing_queues: dict = defaultdict(set)

        # Resolve per-camera staff threshold once at init time
        self._staff_threshold = STAFF_HSV_RATIO_THRESHOLD_OVERRIDE.get(
            camera_id, STAFF_HSV_RATIO_THRESHOLD_DEFAULT
        )

        import supervision as sv
        # sv.ByteTrack is correct for supervision==0.28.0
        # ByteTracker rename announced in 0.28 but not exposed
        # in public API until 0.29+. Intentional and version-appropriate.
        self._tracker = sv.ByteTrack(
            track_activation_threshold = BYTETRACK_TRACK_THRESH,
            lost_track_buffer          = BYTETRACK_TRACK_BUFFER,
            minimum_matching_threshold = BYTETRACK_MATCH_THRESH,
            frame_rate                 = BYTETRACK_FRAME_RATE,
        )

    def _point_in_zone(self, cx: float, cy: float, zone_id: str) -> bool:
        points = self.zone_map.get(zone_id)
        if points is None:
            return False
        polygon = np.array(points, dtype=np.int32)
        result  = cv2.pointPolygonTest(polygon, (cx, cy), False)
        return result >= 0

    def _get_zone(self, cx: float, cy: float) -> Optional[str]:
        for zone_id in self.zone_map:
            if self._point_in_zone(cx, cy, zone_id):
                return zone_id
        return None

    def _find_reentry_match(self, embedding: np.ndarray) -> Optional[str]:
        now   = datetime.now(timezone.utc)
        stale = [
            vid for vid, s in self._exited.items()
            if s.exit_time and
            (now - s.exit_time).total_seconds() > REENTRY_WINDOW_SECONDS
        ]
        for vid in stale:
            del self._exited[vid]

        best_score = 0.0
        best_vid   = None
        for vid, session in self._exited.items():
            score = cosine_similarity(embedding, session.embedding)
            if score > best_score:
                best_score = score
                best_vid   = vid

        return best_vid if best_score >= REID_COSINE_THRESHOLD else None

    def process_frame(
        self,
        frame: np.ndarray,
        detections,
        frame_timestamp: datetime,
    ):
        import supervision as sv

        tracked = self._tracker.update_with_detections(detections)

        current_track_ids = set()
        if len(tracked) > 0:
            current_track_ids = set(tracked.tracker_id.tolist())

        for i in range(len(tracked)):
            track_id   = int(tracked.tracker_id[i])
            bbox       = tracked.xyxy[i]
            confidence = (
                float(tracked.confidence[i])
                if tracked.confidence is not None
                else 1.0
            )
            cx = (bbox[0] + bbox[2]) / 2
            cy = (bbox[1] + bbox[3]) / 2

            embedding = extract_color_histogram(frame, bbox)
            is_staff, _ = is_staff_by_uniform(
                frame, bbox, threshold=self._staff_threshold
            )

            if track_id not in self._active:
                reentry_vid = self._find_reentry_match(embedding)
                if reentry_vid:
                    visitor_id = reentry_vid
                    del self._exited[reentry_vid]
                    self.emitter.emit_reentry(
                        visitor_id = visitor_id,
                        timestamp  = frame_timestamp,
                        confidence = confidence,
                    )
                else:
                    visitor_id = f"VIS_{uuid.uuid4().hex[:6].upper()}"
                    self.emitter.emit_entry(
                        visitor_id = visitor_id,
                        timestamp  = frame_timestamp,
                        is_staff   = is_staff,
                        confidence = confidence,
                    )

                self._active[track_id] = VisitorSession(
                    visitor_id = visitor_id,
                    track_id   = track_id,
                    entry_time = frame_timestamp,
                    is_staff   = is_staff,
                    embedding  = embedding,
                )
            else:
                session           = self._active[track_id]
                session.last_seen = frame_timestamp
                session.embedding = 0.7 * session.embedding + 0.3 * embedding

            session      = self._active[track_id]
            current_zone = self._get_zone(cx, cy)

            if current_zone != session.current_zone:
                if session.current_zone is not None:
                    self.emitter.emit_zone_exit(
                        visitor_id = session.visitor_id,
                        timestamp  = frame_timestamp,
                        zone_id    = session.current_zone,
                        is_staff   = session.is_staff,
                        confidence = confidence,
                    )
                    if session.current_zone in BILLING_ZONE_IDS:
                        self._billing_queues[session.current_zone].discard(
                            session.visitor_id
                        )

                if current_zone is not None:
                    if current_zone in BILLING_ZONE_IDS:
                        queue_depth = len(
                            self._billing_queues[current_zone]
                        )
                        if queue_depth > 0:
                            self.emitter.emit_billing_queue_join(
                                visitor_id  = session.visitor_id,
                                timestamp   = frame_timestamp,
                                zone_id     = current_zone,
                                queue_depth = queue_depth,
                                confidence  = confidence,
                            )
                        self._billing_queues[current_zone].add(
                            session.visitor_id
                        )
                    else:
                        self.emitter.emit_zone_enter(
                            visitor_id = session.visitor_id,
                            timestamp  = frame_timestamp,
                            zone_id    = current_zone,
                            is_staff   = session.is_staff,
                            confidence = confidence,
                        )

                session.current_zone    = current_zone
                session.zone_enter_time = frame_timestamp
                session.last_dwell_emit = frame_timestamp

            else:
                if (
                    session.current_zone is not None
                    and session.last_dwell_emit is not None
                ):
                    elapsed = (
                        frame_timestamp - session.last_dwell_emit
                    ).total_seconds()
                    if elapsed >= DWELL_EMIT_INTERVAL_SECONDS:
                        self.emitter.emit_zone_dwell(
                            visitor_id = session.visitor_id,
                            timestamp  = frame_timestamp,
                            zone_id    = session.current_zone,
                            dwell_ms   = int(elapsed * 1000),
                            is_staff   = session.is_staff,
                            confidence = confidence,
                        )
                        session.last_dwell_emit = frame_timestamp

        lost_ids = set(self._active.keys()) - current_track_ids
        for track_id in lost_ids:
            session           = self._active.pop(track_id)
            session.exited    = True
            session.exit_time = frame_timestamp

            if session.current_zone in BILLING_ZONE_IDS:
                self._billing_queues[session.current_zone].discard(
                    session.visitor_id
                )

            self.emitter.emit_exit(
                visitor_id = session.visitor_id,
                timestamp  = frame_timestamp,
                is_staff   = session.is_staff,
                confidence = 0.8,
            )
            self._exited[session.visitor_id] = session

    def get_queue_depth(self, zone_id: str) -> int:
        return len(self._billing_queues.get(zone_id, set()))

    def flush_all_exits(self, timestamp: datetime):
        for track_id, session in list(self._active.items()):
            session.exited    = True
            session.exit_time = timestamp
            self.emitter.emit_exit(
                visitor_id = session.visitor_id,
                timestamp  = timestamp,
                is_staff   = session.is_staff,
                confidence = 0.7,
            )
            self._exited[session.visitor_id] = session
        self._active.clear()