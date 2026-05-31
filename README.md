\# Store Intelligence System

\## Purplle Tech Challenge 2026



An end-to-end retail analytics pipeline: raw CCTV footage → live store metrics API.



\---



\## Quick Start (5 commands)



```bash

\# 1. Clone and enter

git clone <your-repo-url> store-intelligence \&\& cd store-intelligence



\# 2. Start the API

docker compose up --build



\# 3. Verify health

curl http://localhost:8000/health



\# 4. Verify metrics endpoint

curl http://localhost:8000/stores/STORE\_BLR\_002/metrics



\# 5. Run tests

docker compose exec api pytest tests/ -v

```



The API is live at `http://localhost:8000` after step 2.

Interactive docs at `http://localhost:8000/docs`.



\---



\## Running the Detection Pipeline



\### Setup (one time)

```bash

conda create -n purplle python=3.11 -y

conda activate purplle

pip install -r requirements.txt

```



\### Process a single clip

```bash

python -m pipeline.detect \\

&#x20; --clip    data/clips/store\_blr\_002\_entry.mp4 \\

&#x20; --store   STORE\_BLR\_002 \\

&#x20; --camera  CAM\_ENTRY\_01 \\

&#x20; --layout  data/store\_layout.json \\

&#x20; --output  data/events/STORE\_BLR\_002\_CAM\_ENTRY\_01.jsonl \\

&#x20; --start   2026-03-03T09:00:00Z

```



\### Process all clips at once

```bash

bash pipeline/run.sh

```



Events are written to `data/events/` as `.jsonl` files.



\### Feed events into the API

```bash

\# The pipeline writes events to data/events/\*.jsonl

\# Feed them into the API:

python -c "

import json, urllib.request



with open('data/events/STORE\_BLR\_002\_CAM\_ENTRY\_01.jsonl') as f:

&#x20;   events = \[json.loads(line) for line in f if line.strip()]



\# Send in batches of 500

for i in range(0, len(events), 500):

&#x20;   batch = events\[i:i+500]

&#x20;   data  = json.dumps({'events': batch}).encode()

&#x20;   req   = urllib.request.Request(

&#x20;       'http://localhost:8000/events/ingest',

&#x20;       data    = data,

&#x20;       headers = {'Content-Type': 'application/json'},

&#x20;       method  = 'POST',

&#x20;   )

&#x20;   with urllib.request.urlopen(req) as resp:

&#x20;       print(json.loads(resp.read()))

"

```



\---



\## Live Dashboard (Part E)



Start the API first, then run:

```bash

python dashboard/dashboard.py --store STORE\_BLR\_002 --api http://localhost:8000

```



The dashboard refreshes every 5 seconds showing:

\- Real-time metrics (visitors, conversion rate, queue depth)

\- Conversion funnel with drop-off percentages

\- Zone heatmap with dwell times

\- Active anomalies with severity and suggested actions



\---



\## API Endpoints



| Method | Endpoint | Description |

|---|---|---|

| POST | `/events/ingest` | Ingest batch of events (max 500, idempotent) |

| GET | `/stores/{id}/metrics` | Unique visitors, conversion, dwell, queue |

| GET | `/stores/{id}/funnel` | Entry → Zone → Billing → Purchase funnel |

| GET | `/stores/{id}/heatmap` | Zone visit frequency normalised 0-100 |

| GET | `/stores/{id}/anomalies` | Queue spike, conversion drop, dead zones |

| GET | `/health` | Service status + feed freshness per store |



Full interactive docs: `http://localhost:8000/docs`



\---



\## Running Tests



```bash

\# With coverage report

pytest tests/ -v



\# Expected: 20 passed, coverage >80%

```



\---



\## Project Structure



store-intelligence/

├── pipeline/

│   ├── detect.py      # Main detection + tracking script

│   ├── tracker.py     # Re-ID / ByteTrack / zone assignment

│   ├── emit.py        # Event schema + emission

│   ├── config.py      # All tunable parameters

│   └── run.sh         # One command to process all clips

├── app/

│   ├── main.py        # FastAPI entrypoint + middleware

│   ├── models.py      # Pydantic schema + SQLAlchemy models

│   ├── ingestion.py   # Ingest, validate, deduplicate

│   ├── metrics.py     # Real-time metric computation

│   ├── funnel.py      # Funnel + session logic

│   ├── anomalies.py   # Anomaly detection (3 types)

│   └── health.py      # Health + feed freshness

├── tests/

│   ├── test\_ingestion.py   # 11 tests — schema, idempotency, edge cases

│   └── test\_anomalies.py   # 9 tests  — all 3 anomaly detectors

├── docs/

│   ├── DESIGN.md      # Architecture + AI-assisted decisions

│   └── CHOICES.md     # 3 decisions with full reasoning

├── dashboard/

│   └── dashboard.py   # Live terminal dashboard (Part E)

├── data/

│   ├── clips/         # CCTV video files (not in repo)

│   ├── events/        # Generated .jsonl event files (not in repo)

│   └── pos/           # POS transaction CSV (not in repo)

├── Dockerfile

├── docker-compose.yml

├── requirements.txt

└── .env



\---



\## Edge Cases Handled



| Edge Case | Implementation |

|---|---|

| Group entry | Each ByteTrack track\_id is independent — 3 people = 3 ENTRY events |

| Staff movement | HSV uniform color classifier; `is\_staff=true` excluded from all metrics |

| Re-entry | Cosine similarity Re-ID within 300s window; emits REENTRY not second ENTRY |

| Partial occlusion | Confidence passthrough — low-conf events emitted, never silently dropped |

| Billing queue | Per-zone visitor set; `queue\_depth` on BILLING\_QUEUE\_JOIN events |

| Empty store | Safe zero returns on all metrics; no nulls, no crashes |

| Camera overlap | Funnel stage capping prevents impossible stage2 > stage1 counts |



\---



\## Architecture Decisions



See `docs/DESIGN.md` for full architecture and `docs/CHOICES.md` for the three

key decisions: model selection, Re-ID strategy, and storage engine choice.



