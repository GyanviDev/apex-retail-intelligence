#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# run.sh — Process all CCTV clips for ST1008 Brigade_Bangalore
# Usage: bash pipeline/run.sh
# Requires: conda activate purplle
# ─────────────────────────────────────────────────────────────

set -e

STORE_ID="ST1008"
LAYOUT="data/store_layout.json"
CLIPS_DIR="data/clips"
EVENTS_DIR="data/events"

mkdir -p "$EVENTS_DIR"

echo "========================================"
echo " Store Intelligence Pipeline"
echo " Store: $STORE_ID (Brigade_Bangalore)"
echo " Starting clip processing..."
echo "========================================"

# ── Entry/Exit Camera ─────────────────────────────────────────
python -m pipeline.detect \
  --clip    "$CLIPS_DIR/entry.mp4" \
  --store   "$STORE_ID" \
  --camera  CAM_ENTRY_01 \
  --layout  "$LAYOUT" \
  --output  "$EVENTS_DIR/${STORE_ID}_CAM_ENTRY_01.jsonl" \
  --start   2026-04-10T10:00:00Z

# ── Main Floor Camera ─────────────────────────────────────────
python -m pipeline.detect \
  --clip    "$CLIPS_DIR/floor.mp4" \
  --store   "$STORE_ID" \
  --camera  CAM_FLOOR_01 \
  --layout  "$LAYOUT" \
  --output  "$EVENTS_DIR/${STORE_ID}_CAM_FLOOR_01.jsonl" \
  --start   2026-04-10T10:00:00Z

# ── Billing Area Camera ───────────────────────────────────────
python -m pipeline.detect \
  --clip    "$CLIPS_DIR/billing.mp4" \
  --store   "$STORE_ID" \
  --camera  CAM_BILLING_01 \
  --layout  "$LAYOUT" \
  --output  "$EVENTS_DIR/${STORE_ID}_CAM_BILLING_01.jsonl" \
  --start   2026-04-10T10:00:00Z

echo "========================================"
echo " All clips processed."
echo " Events written to: $EVENTS_DIR"
echo " Next: feed events into API with:"
echo "   python pipeline/feed_events.py"
echo "========================================"