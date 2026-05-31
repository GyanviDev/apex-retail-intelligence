"""
Pipeline configuration — loads from .env file.
Single place to change all tunable parameters.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# Detection
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.35"))
IOU_THRESHOLD        = float(os.getenv("IOU_THRESHOLD", "0.45"))

# Re-ID / Tracking
REENTRY_WINDOW_SECONDS = int(os.getenv("REENTRY_WINDOW_SECONDS", "300"))
STAFF_EXCLUSION_ENABLED = os.getenv("STAFF_EXCLUSION_ENABLED", "true").lower() == "true"

# Event emission
DWELL_EMIT_INTERVAL_SECONDS      = int(os.getenv("DWELL_EMIT_INTERVAL_SECONDS", "30"))
BILLING_ZONE_CORRELATION_WINDOW  = int(os.getenv("BILLING_ZONE_CORRELATION_WINDOW", "300"))
MAX_BATCH_SIZE                   = int(os.getenv("MAX_BATCH_SIZE", "500"))

# Camera and zone config
ENTRY_EXIT_CAMERAS  = ["CAM_ENTRY_01", "CAM_ENTRY_02"]
BILLING_ZONE_IDS    = ["BILLING", "BILLING_COUNTER", "CHECKOUT"]

# Staff detection — uniform HSV color ranges (saffron/orange typical retail uniform)
# These are tunable per store deployment
STAFF_UNIFORM_HSV_LOWER = (5,  100, 100)
STAFF_UNIFORM_HSV_UPPER = (25, 255, 255)

# ByteTrack parameters
BYTETRACK_TRACK_THRESH     = 0.45
BYTETRACK_TRACK_BUFFER     = 30
BYTETRACK_MATCH_THRESH     = 0.8
BYTETRACK_FRAME_RATE       = 15

# Re-ID appearance similarity threshold
REID_COSINE_THRESHOLD = 0.65