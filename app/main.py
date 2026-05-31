"""
Store Intelligence API — FastAPI entrypoint.
Purplle Tech Challenge 2026

Endpoints:
  POST /events/ingest
  GET  /stores/{store_id}/metrics
  GET  /stores/{store_id}/funnel
  GET  /stores/{store_id}/heatmap
  GET  /stores/{store_id}/anomalies
  GET  /health
"""

import time
import uuid
import logging
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.models import (
    IngestResponse,
    init_db,
    get_db,
)
from app.ingestion  import ingest_batch
from app.metrics    import get_store_metrics, get_heatmap
from app.funnel     import get_funnel
from app.anomalies  import get_anomalies
from app.health     import get_health

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ── Lifespan: DB init on startup ──────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Store Intelligence API...")
    init_db()
    logger.info("Database initialized.")
    yield
    logger.info("Shutting down Store Intelligence API.")


# ── App factory ───────────────────────────────────────────────────────────────
app = FastAPI(
    title       = "Store Intelligence API",
    description = "Purplle Tech Challenge 2026 — Retail Analytics",
    version     = "1.0.0",
    lifespan    = lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins = ["*"],
    allow_methods = ["*"],
    allow_headers = ["*"],
)


# ── Request logging middleware ─────────────────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """
    Structured request logging per spec:
    trace_id, store_id, endpoint, latency_ms, status_code
    """
    trace_id   = str(uuid.uuid4())[:8]
    start_time = time.monotonic()

    path_parts = request.url.path.split("/")
    store_id   = None
    if "stores" in path_parts:
        idx = path_parts.index("stores")
        if idx + 1 < len(path_parts):
            store_id = path_parts[idx + 1]

    request.state.trace_id = trace_id

    try:
        response = await call_next(request)
    except Exception as e:
        logger.error(
            f"trace_id={trace_id} "
            f"endpoint={request.url.path} "
            f"error={str(e)}"
        )
        return JSONResponse(
            status_code = 500,
            content     = {
                "error":    "INTERNAL_ERROR",
                "trace_id": trace_id,
                "detail":   "An unexpected error occurred.",
            },
        )

    latency_ms = round((time.monotonic() - start_time) * 1000, 2)

    logger.info(
        f"trace_id={trace_id} "
        f"store_id={store_id} "
        f"endpoint={request.url.path} "
        f"method={request.method} "
        f"status={response.status_code} "
        f"latency_ms={latency_ms}"
    )

    response.headers["X-Trace-ID"]   = trace_id
    response.headers["X-Latency-MS"] = str(latency_ms)
    return response


# ── Global exception handler — no raw stack traces ────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    trace_id = getattr(request.state, "trace_id", "unknown")
    logger.error(
        f"Unhandled exception trace_id={trace_id}: "
        f"{traceback.format_exc()}"
    )
    return JSONResponse(
        status_code = 500,
        content     = {
            "error":    "INTERNAL_ERROR",
            "trace_id": trace_id,
            "detail":   "An unexpected error occurred.",
        },
    )


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.post(
    "/events/ingest",
    response_model = IngestResponse,
    summary        = "Ingest a batch of store events",
)
async def ingest_events(
    request: Request,
    db:      Session = Depends(get_db),
):
    """
    Accepts batches of up to 500 events.
    Idempotent by event_id — safe to call twice with same payload.
    Partial success — malformed events are rejected individually.
    """
    trace_id = getattr(request.state, "trace_id", "unknown")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail={
            "error":  "INVALID_JSON",
            "detail": "Request body must be valid JSON.",
        })

    raw_events = body.get("events", [])

    if not isinstance(raw_events, list) or len(raw_events) == 0:
        raise HTTPException(status_code=422, detail={
            "error":  "INVALID_BATCH",
            "detail": "events must be a non-empty list.",
        })

    if len(raw_events) > 500:
        raise HTTPException(status_code=422, detail={
            "error":  "BATCH_TOO_LARGE",
            "detail": f"Batch size {len(raw_events)} exceeds maximum of 500.",
        })

    result = ingest_batch(raw_events, db)

    logger.info(
        f"trace_id={trace_id} "
        f"event_count={len(raw_events)} "
        f"accepted={result.accepted} "
        f"rejected={result.rejected} "
        f"duplicate={result.duplicate}"
    )
    return result


@app.get(
    "/stores/{store_id}/metrics",
    summary = "Real-time store metrics",
)
async def store_metrics(
    store_id: str,
    db:       Session = Depends(get_db),
):
    """
    Returns: unique visitors, conversion rate, avg dwell per zone,
    queue depth, abandonment rate.
    Excludes staff. Handles zero-traffic stores.
    """
    try:
        return get_store_metrics(db, store_id)
    except Exception as e:
        logger.error(f"Metrics error for {store_id}: {e}")
        raise HTTPException(status_code=503, detail={
            "error":  "METRICS_UNAVAILABLE",
            "detail": str(e),
        })


@app.get(
    "/stores/{store_id}/funnel",
    summary = "Conversion funnel",
)
async def store_funnel(
    store_id: str,
    db:       Session = Depends(get_db),
):
    """
    Conversion funnel: Entry → Zone Visit → Billing Queue → Purchase.
    Session is the unit. Re-entries do not double-count.
    """
    try:
        return get_funnel(db, store_id)
    except Exception as e:
        logger.error(f"Funnel error for {store_id}: {e}")
        raise HTTPException(status_code=503, detail={
            "error":  "FUNNEL_UNAVAILABLE",
            "detail": str(e),
        })


@app.get(
    "/stores/{store_id}/heatmap",
    summary = "Zone visit heatmap",
)
async def store_heatmap(
    store_id: str,
    db:       Session = Depends(get_db),
):
    """
    Zone visit frequency + avg dwell, normalised 0-100.
    Includes data_confidence flag if fewer than 20 sessions.
    """
    try:
        return get_heatmap(db, store_id)
    except Exception as e:
        logger.error(f"Heatmap error for {store_id}: {e}")
        raise HTTPException(status_code=503, detail={
            "error":  "HEATMAP_UNAVAILABLE",
            "detail": str(e),
        })


@app.get(
    "/stores/{store_id}/anomalies",
    summary = "Active anomalies",
)
async def store_anomalies(
    store_id: str,
    db:       Session = Depends(get_db),
):
    """
    Active anomalies: queue spike, conversion drop, dead zone.
    Severity: INFO / WARN / CRITICAL.
    Each anomaly includes suggested_action.
    """
    try:
        return get_anomalies(db, store_id)
    except Exception as e:
        logger.error(f"Anomalies error for {store_id}: {e}")
        raise HTTPException(status_code=503, detail={
            "error":  "ANOMALIES_UNAVAILABLE",
            "detail": str(e),
        })


@app.get(
    "/health",
    summary = "Service health",
)
async def health_check(
    db: Session = Depends(get_db),
):
    """
    Service status + last event timestamp per store.
    STALE_FEED warning if >10 min lag.
    Never returns 500 — DB errors return 503 with structured body.
    """
    try:
        result      = get_health(db)
        status_code = 200 if result["status"] == "OK" else 503
        return JSONResponse(content=result, status_code=status_code)
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(
            status_code = 503,
            content     = {
                "status":     "DEGRADED",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "error":      "Database unavailable",
                "version":    "1.0.0",
            },
        )