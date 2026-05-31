"""
Main detection pipeline.
Orchestrates: video reading → YOLO detection → ByteTrack → event emission.

Usage:
    python -m pipeline.detect --clip data/clips/store_blr_entry.mp4
                              --store STORE_BLR_002
                              --camera CAM_ENTRY_01
                              --layout data/store_layout.json
                              --output data/events/store_blr_002.jsonl
"""

import cv2
import json
import argparse
import os
import numpy as np
from datetime import datetime, timezone, timedelta
from ultralytics import YOLO
import supervision as sv

from pipeline.tracker import StoreTracker
from pipeline.emit import EventEmitter
from pipeline.config import (
    CONFIDENCE_THRESHOLD,
    IOU_THRESHOLD,
    BYTETRACK_FRAME_RATE,
)


def load_zone_map(layout_path: str, store_id: str, camera_id: str) -> dict:
    """
    Load zone polygon definitions from store_layout.json.
    Returns {zone_id: [[x1,y1],[x2,y2],...]} for the given camera.
    Falls back to empty dict if camera not found — pipeline continues
    without zone assignment rather than crashing.
    """
    if not os.path.exists(layout_path):
        print(f"[WARN] Layout file not found: {layout_path}. Zone mapping disabled.")
        return {}

    with open(layout_path, "r") as f:
        layout = json.load(f)

    stores = layout if isinstance(layout, list) else [layout]
    for store in stores:
        if store.get("store_id") != store_id:
            continue
        cameras = store.get("cameras", {})
        zones   = cameras.get(camera_id, {}).get("zones", {})
        if zones:
            print(f"[INFO] Loaded {len(zones)} zones for {camera_id}")
            return zones
        # Fallback: try top-level zones key
        zones = store.get("zones", {})
        return zones

    print(f"[WARN] Store {store_id} not found in layout. Zone mapping disabled.")
    return {}


def frame_to_timestamp(clip_start: datetime, frame_idx: int, fps: float) -> datetime:
    """
    Convert frame index to UTC timestamp.
    clip_start is the wall-clock time the clip recording began.
    """
    offset_seconds = frame_idx / fps
    return clip_start + timedelta(seconds=offset_seconds)


def run_detection(
    clip_path:    str,
    store_id:     str,
    camera_id:    str,
    layout_path:  str,
    output_path:  str,
    clip_start:   datetime = None,
    show_preview: bool     = False,
):
    """
    Main detection loop for a single clip.

    Args:
        clip_path:    Path to the .mp4 file
        store_id:     Store identifier (e.g. STORE_BLR_002)
        camera_id:    Camera identifier (e.g. CAM_ENTRY_01)
        layout_path:  Path to store_layout.json
        output_path:  Path to write .jsonl events
        clip_start:   UTC datetime for frame 0. Defaults to now.
        show_preview: Show OpenCV window (for debugging only)
    """
    if clip_start is None:
        clip_start = datetime.now(timezone.utc)

    print(f"[INFO] Loading YOLO model...")
    model = YOLO("yolov8n.pt")  # nano — fast on CPU, good enough for 1080p/15fps
    print(f"[INFO] YOLO loaded. Processing: {clip_path}")

    cap = cv2.VideoCapture(clip_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {clip_path}")

    fps         = cap.get(cv2.CAP_PROP_FPS) or BYTETRACK_FRAME_RATE
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"[INFO] FPS: {fps} | Total frames: {total_frames}")

    zone_map = load_zone_map(layout_path, store_id, camera_id)

    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)

    with EventEmitter(store_id, camera_id, output_path) as emitter:
        tracker = StoreTracker(store_id, camera_id, zone_map, emitter)

        frame_idx     = 0
        events_total  = 0
        log_interval  = int(fps * 30)  # log every 30 seconds of video

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            frame_timestamp = frame_to_timestamp(clip_start, frame_idx, fps)

            # ── YOLO inference ────────────────────────────────────────────
            results = model(
                frame,
                classes   = [0],          # class 0 = person only
                conf      = CONFIDENCE_THRESHOLD,
                iou       = IOU_THRESHOLD,
                verbose   = False,
            )[0]

            # ── Convert to supervision Detections ─────────────────────────
            detections = sv.Detections.from_ultralytics(results)

            # ── Process frame through tracker ─────────────────────────────
            tracker.process_frame(frame, detections, frame_timestamp)

            # ── Progress logging ──────────────────────────────────────────
            if frame_idx % log_interval == 0:
                progress = (frame_idx / total_frames * 100) if total_frames > 0 else 0
                print(f"[INFO] Frame {frame_idx}/{total_frames} "
                      f"({progress:.1f}%) | "
                      f"Active tracks: {len(tracker._active)} | "
                      f"Timestamp: {frame_timestamp.strftime('%H:%M:%S')}")

            if show_preview:
                annotated = results.plot()
                cv2.imshow("Detection Preview", annotated)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

            frame_idx += 1

        # ── End of clip — flush remaining sessions ────────────────────────
        final_timestamp = frame_to_timestamp(clip_start, frame_idx, fps)
        tracker.flush_all_exits(final_timestamp)
        print(f"[INFO] Processing complete. "
              f"Frames: {frame_idx} | "
              f"Output: {output_path}")

    cap.release()
    if show_preview:
        cv2.destroyAllWindows()
def main():
    parser = argparse.ArgumentParser(
        description="Purplle Store Intelligence — Detection Pipeline"
    )
    parser.add_argument("--clip",    required=True,  help="Path to video clip")
    parser.add_argument("--store",   required=True,  help="Store ID e.g. STORE_BLR_002")
    parser.add_argument("--camera",  required=True,  help="Camera ID e.g. CAM_ENTRY_01")
    parser.add_argument("--layout",  default="data/store_layout.json",
                        help="Path to store_layout.json")
    parser.add_argument("--output",  default=None,
                        help="Output .jsonl path. Defaults to data/events/<store>_<camera>.jsonl")
    parser.add_argument("--start",   default=None,
                        help="Clip start time ISO-8601 UTC e.g. 2026-03-03T14:00:00Z")
    parser.add_argument("--preview", action="store_true",
                        help="Show OpenCV preview window")

    args = parser.parse_args()

    # Default output path
    if args.output is None:
        os.makedirs("data/events", exist_ok=True)
        safe_camera = args.camera.replace("/", "_").replace("\\", "_")
        args.output = f"data/events/{args.store}_{safe_camera}.jsonl"

    # Parse clip start time
    clip_start = None
    if args.start:
        clip_start = datetime.fromisoformat(args.start.replace("Z", "+00:00"))

    run_detection(
        clip_path   = args.clip,
        store_id    = args.store,
        camera_id   = args.camera,
        layout_path = args.layout,
        output_path = args.output,
        clip_start  = clip_start,
        show_preview= args.preview,
    )


if __name__ == "__main__":
    main()