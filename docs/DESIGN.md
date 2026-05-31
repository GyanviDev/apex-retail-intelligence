\# DESIGN.md — Store Intelligence System

\## Purplle Tech Challenge 2026



\---



\## 1. System Overview



This system transforms raw CCTV footage into a live retail analytics API.

The pipeline has four stages:



Raw Video → Detection Layer → Event Stream → Intelligence API → Live Dashboard



Every architectural decision traces back to one north star metric:



\*\*Offline Store Conversion Rate = Visitors who purchased ÷ Total unique visitors\*\*



\---



\## 2. Architecture Diagram



┌─────────────────────────────────────────────────────────────┐

│                     DETECTION LAYER                         │

│                                                             │

│  CCTV Clip (.mp4)                                           │

│      │                                                      │

│      ▼                                                      │

│  YOLOv8n ──► person detections (class=0 only)               │

│      │                                                      │

│      ▼                                                      │

│  supervision ByteTrack ──► track\_id per person              │

│      │                                                      │

│      ▼                                                      │

│  StoreTracker                                               │

│    ├── HSV uniform classifier ──► is\_staff flag             │

│    ├── Color histogram Re-ID ──► re-entry detection         │

│    ├── Polygon zone assignment ──► zone\_id per frame        │

│    ├── Dwell timer ──► ZONE\_DWELL every 30s                 │

│    └── Billing queue counter ──► queue\_depth                │

│      │                                                      │

│      ▼                                                      │

│  EventEmitter ──► .jsonl file (one event per line)          │

└─────────────────────────────────────────────────────────────┘

│

▼ POST /events/ingest

┌─────────────────────────────────────────────────────────────┐

│                    INTELLIGENCE API                         │

│                                                             │

│  FastAPI + SQLAlchemy + SQLite                              │

│                                                             │

│  POST /events/ingest     ──► validate, deduplicate, store   │

│  GET  /stores/{id}/metrics   ──► real-time aggregation      │

│  GET  /stores/{id}/funnel    ──► session-based funnel       │

│  GET  /stores/{id}/heatmap   ──► zone frequency + dwell     │

│  GET  /stores/{id}/anomalies ──► 3 anomaly detectors        │

│  GET  /health                ──► feed freshness + status    │

└─────────────────────────────────────────────────────────────┘

│

▼

┌─────────────────────────────────────────────────────────────┐

│                   LIVE DASHBOARD                            │

│  Rich terminal UI — polls API every 5s                      │

│  4 panels: Metrics │ Funnel │ Heatmap │ Anomalies           │

└─────────────────────────────────────────────────────────────┘



\---



\## 3. Edge Case Handling



Every edge case listed in the problem statement is explicitly handled:



| Edge Case | Where Handled | Mechanism |

|---|---|---|

| Group entry | `tracker.py:process\_frame()` | Each ByteTrack `track\_id` is independent — 3 people = 3 ENTRY events |

| Staff movement | `tracker.py:is\_staff\_by\_uniform()` | HSV color threshold on torso crop; `is\_staff=true` flag propagated to all events |

| Re-entry | `tracker.py:\_find\_reentry\_match()` | Cosine similarity on color histogram embeddings vs exited visitor pool within 300s window |

| Partial occlusion | `detect.py` + schema | `confidence` field passed through at all values ≥ 0.35; never silently dropped |

| Billing queue buildup | `tracker.py:\_billing\_queues` | Per-zone set of active visitor\_ids; `queue\_depth` populated on `BILLING\_QUEUE\_JOIN` |

| Empty store | `tracker.py:process\_frame()` | Zero detections → ByteTrack updates lost buffer → EXIT events emitted; API returns safe zeros |

| Camera overlap | `funnel.py` + `visitor\_id` design | Funnel caps each stage at previous stage; visitor\_id is globally unique UUID-based token |



\---



\## 4. The visitor\_id Contract



The `visitor\_id` is the most critical field in the entire system. It is a promise:

\*\*one unique human = one stable visitor\_id per visit session.\*\*



Breaking this promise cascades into every downstream metric:

\- Track ID switch → one visitor counted as two → conversion rate deflated

\- Re-entry not caught → unique visitors inflated → conversion rate deflated

\- Group detected as one blob → entry count = 1 but 3 POS transactions → conversion > 100%



Our implementation maintains this contract through three mechanisms:

1\. ByteTrack maintains track continuity across occlusion frames

2\. Re-ID via color histogram cosine similarity catches re-entries within 300s

3\. `flush\_all\_exits()` at clip end closes all open sessions, preventing dangling visitor\_ids



\---



\## 5. AI-Assisted Decisions



\### 5.1 Event Schema Field Validators

Claude suggested adding cross-field Pydantic validators for `zone\_id` and `dwell\_ms`.

Specifically: ZONE\_DWELL events must have `dwell\_ms > 0`, and zone-type events must have

a non-null `zone\_id`. I agreed and implemented both validators in `app/models.py`.

This caught a real bug during testing where ZONE\_DWELL events with `dwell\_ms=0` were

being silently accepted. The validators surface these errors at ingestion time rather

than corrupting the dwell average metrics silently.



\### 5.2 Funnel Stage Capping

When designing the funnel, Claude flagged the camera overlap edge case: a visitor

detected by both the entry camera and the floor camera could produce a ZONE\_ENTER

event without a corresponding ENTRY event from the entry camera's perspective.

This would make stage 2 > stage 1 — an impossible funnel shape. Claude suggested

capping each stage at the previous stage's count. I agreed and implemented

`stage2 = min(stage2, stage1)` in `app/funnel.py`.



\### 5.3 Re-ID Strategy

Claude initially suggested using a full OSNet torchreid model for appearance-based

Re-ID. I overrode this decision and chose color histogram cosine similarity instead.

Reasoning: OSNet requires GPU inference and adds a 400MB+ model download dependency.

On a CPU-only deployment target, OSNet runs at \~1-2 FPS — far too slow for 15fps

video. Color histograms run in microseconds per frame. The accuracy trade-off is

acceptable because retail environments have relatively stable lighting and staff

uniforms create strong discriminative color signals. This decision is documented

in CHOICES.md.



\---



\## 6. Database Design



SQLite was chosen for zero-infrastructure deployment. The `events` table has two

composite indexes designed for the exact query patterns the API uses:



\- `ix\_store\_time (store\_id, timestamp)` — powers all `/metrics` queries which

&#x20; filter by store and today's date range

\- `ix\_visitor\_store (visitor\_id, store\_id)` — powers funnel deduplication which

&#x20; groups by visitor within a store



Without these indexes, every metric query does a full table scan. At 40 stores

with 15fps × 3 cameras × 20 minutes of footage, the events table reaches \~1M rows.

Full table scans at that scale would make the API unusable.



\---



\## 7. Production Readiness Decisions



| Requirement | Implementation |

|---|---|

| No raw stack traces | Global exception handler in `main.py` returns structured JSON |

| DB unavailable → 503 | Every endpoint wrapped in try/except; health returns DEGRADED |

| Idempotency | `check\_duplicate()` in `ingestion.py` queries by `event\_id` before insert |

| Structured logging | Every request logs `trace\_id, store\_id, endpoint, latency\_ms, status\_code` |

| Test coverage >70% | 20 tests, 80.99% coverage including all 7 edge cases |

| docker compose up | Single command starts API with health check and volume persistence |

