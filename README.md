# Store Intelligence System

## Purplle Tech Challenge 2026

An end-to-end retail analytics pipeline that transforms raw CCTV footage into live retail analytics through a real-time metrics API.

---

# Quick Start (5 Commands)

```bash
# 1. Clone the repository
git clone <your-repo-url> store-intelligence
cd store-intelligence

# 2. Start the API
docker compose up --build

# 3. Verify health
curl http://localhost:8000/health

# 4. Verify metrics endpoint
curl http://localhost:8000/stores/STORE_BLR_002/metrics

# 5. Run tests
docker compose exec api pytest tests/ -v
```

After starting the services, the API will be available at:

- **API:** http://localhost:8000
- **Swagger Docs:** http://localhost:8000/docs

---

# Running the Detection Pipeline

## One-Time Setup

```bash
conda create -n purplle python=3.11 -y
conda activate purplle
pip install -r requirements.txt
```

## Process a Single Video Clip

```bash
python -m pipeline.detect \
  --clip data/clips/store_blr_002_entry.mp4 \
  --store STORE_BLR_002 \
  --camera CAM_ENTRY_01 \
  --layout data/store_layout.json \
  --output data/events/STORE_BLR_002_CAM_ENTRY_01.jsonl \
  --start 2026-03-03T09:00:00Z
```

## Process All Clips

```bash
bash pipeline/run.sh
```

Generated event files are written to:

```
data/events/
```

---

# Feed Events into the API

```bash
python -c "
import json
import urllib.request

with open('data/events/STORE_BLR_002_CAM_ENTRY_01.jsonl') as f:
    events = [json.loads(line) for line in f if line.strip()]

for i in range(0, len(events), 500):
    batch = events[i:i+500]
    data = json.dumps({'events': batch}).encode()

    req = urllib.request.Request(
        'http://localhost:8000/events/ingest',
        data=data,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )

    with urllib.request.urlopen(req) as resp:
        print(json.loads(resp.read()))
"
```

The ingestion endpoint accepts batches of up to **500 events** and is **idempotent**.

---

# Live Dashboard (Part E)

Start the API, then launch the dashboard:

```bash
python dashboard/dashboard.py \
  --store STORE_BLR_002 \
  --api http://localhost:8000
```

The dashboard refreshes every **5 seconds** and displays:

- Real-time visitor metrics
- Conversion rate
- Queue depth
- Conversion funnel with drop-off percentages
- Zone heatmap with dwell times
- Active anomalies with severity and suggested actions

---

# API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/events/ingest` | Ingest a batch of events (max 500, idempotent) |
| GET | `/stores/{id}/metrics` | Visitor count, conversion, dwell time, queue depth |
| GET | `/stores/{id}/funnel` | Entry → Zone → Billing → Purchase funnel |
| GET | `/stores/{id}/heatmap` | Zone visit frequency normalized to 0–100 |
| GET | `/stores/{id}/anomalies` | Queue spikes, conversion drops, dead zones |
| GET | `/health` | Service health and event feed freshness |

Interactive API documentation:

```
http://localhost:8000/docs
```

---

# Running Tests

```bash
# Run all tests
pytest tests/ -v

# With Docker
docker compose exec api pytest tests/ -v
```

Expected result:

- ✅ 20 tests passing
- ✅ >80% code coverage

---

# Project Structure

```text
store-intelligence/
├── app/
│   ├── main.py              # FastAPI entrypoint
│   ├── models.py            # Pydantic + SQLAlchemy models
│   ├── ingestion.py         # Validation & deduplication
│   ├── metrics.py           # Live metric computation
│   ├── funnel.py            # Funnel logic
│   ├── anomalies.py         # Anomaly detection
│   └── health.py            # Health endpoints
│
├── pipeline/
│   ├── detect.py            # Detection pipeline
│   ├── tracker.py           # Tracking & Re-ID
│   ├── emit.py              # Event emission
│   ├── config.py            # Configuration
│   └── run.sh               # Batch processing script
│
├── dashboard/
│   └── dashboard.py         # Live terminal dashboard
│
├── tests/
│   ├── test_ingestion.py
│   └── test_anomalies.py
│
├── docs/
│   ├── DESIGN.md
│   └── CHOICES.md
│
├── data/
│   ├── clips/
│   ├── events/
│   └── pos/
│
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env
└── README.md
```

---

# Edge Cases Handled

| Edge Case | Implementation |
|------------|----------------|
| Group entry | Each ByteTrack `track_id` generates an independent `ENTRY` event |
| Staff movement | Uniform-color classifier marks `is_staff=true`; excluded from analytics |
| Re-entry | Cosine similarity Re-ID within a 300-second window emits `REENTRY` |
| Partial occlusion | Low-confidence detections are retained rather than discarded |
| Billing queue | Queue depth computed from `BILLING_QUEUE_JOIN` events |
| Empty store | Metrics safely return zero values without errors |
| Camera overlap | Funnel stage capping prevents impossible stage counts |

---

# Architecture Decisions

See:

- `docs/DESIGN.md` — Overall system architecture
- `docs/CHOICES.md` — Design rationale covering:
  - Model selection
  - Re-identification strategy
  - Storage engine choice
