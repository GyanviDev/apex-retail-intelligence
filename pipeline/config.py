"""
Pipeline configuration — loads from .env file.
Single place to change all tunable parameters.
"""
import os
from dotenv import load_dotenv
load_dotenv()

# Detection
# Raised from 0.35 to 0.42 — eliminates weak ghost detections from
# door reflections and partial occlusions at entry camera
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.42"))
IOU_THRESHOLD        = float(os.getenv("IOU_THRESHOLD", "0.45"))

# Re-ID / Tracking
REENTRY_WINDOW_SECONDS  = int(os.getenv("REENTRY_WINDOW_SECONDS", "300"))
STAFF_EXCLUSION_ENABLED = os.getenv("STAFF_EXCLUSION_ENABLED", "true").lower() == "true"

# Event emission
DWELL_EMIT_INTERVAL_SECONDS      = int(os.getenv("DWELL_EMIT_INTERVAL_SECONDS", "30"))
BILLING_ZONE_CORRELATION_WINDOW  = int(os.getenv("BILLING_ZONE_CORRELATION_WINDOW", "300"))
MAX_BATCH_SIZE                   = int(os.getenv("MAX_BATCH_SIZE", "500"))

# Camera and zone config
ENTRY_EXIT_CAMERAS  = ["CAM_ENTRY_01"]
DEFAULT_STORE_ID    = "ST1008"
BILLING_ZONE_IDS    = ["BILLING", "BILLING_COUNTER", "CHECKOUT", "CASH_COUNTER"]

# Staff detection — uniform HSV color ranges
# Staff wear BLACK uniforms (verified from CAM 1, 2, 5 frame inspection)
# Black in HSV: low saturation, low value
# Upper value tightened from 80 to 65 — prevents dark civilian clothing
# (navy jeans, dark bags) from triggering staff classifier in evening crowds
STAFF_UNIFORM_HSV_LOWER = (0,   0,   0)
STAFF_UNIFORM_HSV_UPPER = (180, 50,  65)

# Per-camera staff ratio threshold overrides
# Reason: CAM_FLOOR_01 bottom-right region (PREMIUM_MAKEUP zone) has mean
# brightness V=61.6 — dark shelving bleeds into person torso crops and
# inflates black-pixel ratio. Stricter threshold corrects this without
# affecting well-lit entry and billing cameras.
# Measured dark-pixel ratio in shadow zone: 42.2% at frame level
# Customer torso crops in that zone average ~35% dark pixels
# Setting floor threshold to 0.55 separates genuine staff (>55% black)
# from customers in dark zones (30-45% black)
STAFF_HSV_RATIO_THRESHOLD_DEFAULT  = 0.40
STAFF_HSV_RATIO_THRESHOLD_OVERRIDE = {
    "CAM_ENTRY_01":   0.40,
    "CAM_FLOOR_01":   0.55,
    "CAM_FLOOR_02":   0.62,
    "CAM_BILLING_01": 0.70,
}

# ByteTrack parameters
# FRAME_RATE corrected from 15 to 30 to match actual video FPS (29.97)
# Kalman filter and lost-track buffer are now correctly sized for real input rate
# TRACK_BUFFER raised from 30 to 45 — gives 1.5s tolerance at 30 FPS
# so a customer pausing behind the door frame is not dropped and re-tracked
BYTETRACK_TRACK_THRESH = 0.45
BYTETRACK_TRACK_BUFFER = 45
BYTETRACK_MATCH_THRESH = 0.8
BYTETRACK_FRAME_RATE   = 30

# Re-ID appearance similarity threshold
REID_COSINE_THRESHOLD = 0.65