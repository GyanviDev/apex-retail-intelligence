#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# run.sh — Process all CCTV clips for ST1008 Brigade_Bangalore
#
# Camera mapping (verified from frame inspection):
#   CAM 1 → CAM_FLOOR_01    (Skincare wall — The Face Shop, Good Vibes, DermDoc)
#   CAM 2 → CAM_FLOOR_02    (Makeup wall — Swiss Beauty, Lakme, Faces Canada)
#   CAM 3 → CAM_ENTRY_01    (Entry/Exit threshold — glass door)
#   CAM 4 → EXCLUDED        (Backroom/stock room — not customer-facing)
#   CAM 5 → CAM_BILLING_01  (Billing counter — POS terminal, Accessories screen)
#
# Usage: bash pipeline/run.sh
# Requires: conda activate purplle
# ─────────────────────────────────────────────────────────────

set -e

STORE_ID="ST1008"
LAYOUT="data/store_layout.json"
CLIPS_DIR="data/clips/CCTV Footage"
EVENTS_DIR="data/events"

mkdir -p "$EVENTS_DIR"

echo "========================================"
echo " Store Intelligence Pipeline"
echo " Store: $STORE_ID (Brigade_Bangalore)"
echo " Date:  10-04-2026"
echo "========================================"

# ── CAM 3: Entry/Exit threshold ───────────────────────────────
echo "[1/4] Processing Entry/Exit camera (CAM 3)..."
python -m pipeline.detect \
  --clip    "data/clips/CCTV Footage/CAM 3.mp4" \
  --store   "$STORE_ID" \
  --camera  CAM_ENTRY_01 \
  --layout  "$LAYOUT" \
  --output  "$EVENTS_DIR/${STORE_ID}_CAM_ENTRY_01.jsonl" \
  --start   2026-04-10T14:30:00Z

# ── CAM 1: Skincare/Korean beauty floor ──────────────────────
echo "[2/4] Processing Floor camera 1 — Skincare wall (CAM 1)..."
python -m pipeline.detect \
  --clip    "data/clips/CCTV Footage/CAM 1.mp4" \
  --store   "$STORE_ID" \
  --camera  CAM_FLOOR_01 \
  --layout  "$LAYOUT" \
  --output  "$EVENTS_DIR/${STORE_ID}_CAM_FLOOR_01.jsonl" \
  --start   2026-04-10T20:10:00Z

# ── CAM 2: Makeup wall floor ─────────────────────────────────
echo "[3/4] Processing Floor camera 2 — Makeup wall (CAM 2)..."
python -m pipeline.detect \
  --clip    "data/clips/CCTV Footage/CAM 2.mp4" \
  --store   "$STORE_ID" \
  --camera  CAM_FLOOR_02 \
  --layout  "$LAYOUT" \
  --output  "$EVENTS_DIR/${STORE_ID}_CAM_FLOOR_02.jsonl" \
  --start   2026-04-10T20:10:00Z

# ── CAM 5: Billing counter ────────────────────────────────────
echo "[4/4] Processing Billing counter camera (CAM 5)..."
python -m pipeline.detect \
  --clip    "data/clips/CCTV Footage/CAM 5.mp4" \
  --store   "$STORE_ID" \
  --camera  CAM_BILLING_01 \
  --layout  "$LAYOUT" \
  --output  "$EVENTS_DIR/${STORE_ID}_CAM_BILLING_01.jsonl" \
  --start   2026-04-10T20:10:00Z

# ── CAM 4: EXCLUDED (backroom) ────────────────────────────────
echo "[SKIP] CAM 4 excluded — backroom/stock room, not customer-facing"

echo "========================================"
echo " All clips processed."
echo " Events written to: $EVENTS_DIR"
echo " Feed events: python pipeline/feed_events.py --store $STORE_ID"
echo " View metrics: curl http://localhost:8000/stores/$STORE_ID/metrics"
echo "========================================"