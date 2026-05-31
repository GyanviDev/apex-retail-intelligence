#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# run.sh — Process all CCTV clips and emit events to data/events/
# Usage: bash pipeline/run.sh
# Requires: conda activate purplle
# ─────────────────────────────────────────────────────────────

set -e  # Exit immediately on any error

LAYOUT="data/store_layout.json"
CLIPS_DIR="data/clips"
EVENTS_DIR="data/events"

mkdir -p "$EVENTS_DIR"

echo "========================================"
echo " Purplle Store Intelligence Pipeline"
echo " Starting clip processing..."
echo "========================================"

# ── STORE_BLR_002 ─────────────────────────────────────────────
python -m pipeline.detect \
  --clip    "$CLIPS_DIR/store_blr_002_entry.mp4" \
  --store   STORE_BLR_002 \
  --camera  CAM_ENTRY_01 \
  --layout  "$LAYOUT" \
  --output  "$EVENTS_DIR/STORE_BLR_002_CAM_ENTRY_01.jsonl" \
  --start   2026-03-03T09:00:00Z

python -m pipeline.detect \
  --clip    "$CLIPS_DIR/store_blr_002_floor.mp4" \
  --store   STORE_BLR_002 \
  --camera  CAM_FLOOR_01 \
  --layout  "$LAYOUT" \
  --output  "$EVENTS_DIR/STORE_BLR_002_CAM_FLOOR_01.jsonl" \
  --start   2026-03-03T09:00:00Z

python -m pipeline.detect \
  --clip    "$CLIPS_DIR/store_blr_002_billing.mp4" \
  --store   STORE_BLR_002 \
  --camera  CAM_BILLING_01 \
  --layout  "$LAYOUT" \
  --output  "$EVENTS_DIR/STORE_BLR_002_CAM_BILLING_01.jsonl" \
  --start   2026-03-03T09:00:00Z

echo "========================================"
echo " All clips processed."
echo " Events written to: $EVENTS_DIR"
echo "========================================"