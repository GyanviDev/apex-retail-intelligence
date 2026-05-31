\# CHOICES.md — Architectural Decision Record

\## Purplle Tech Challenge 2026



Each decision documents: options considered, what AI suggested,

what I chose, and why.



\---



\## Decision 1: Detection Model Selection



\### Options Considered



| Model | Pros | Cons |

|---|---|---|

| YOLOv8n | Fast on CPU, 6.3M params, well-documented | Lower mAP than larger variants |

| YOLOv8m | Better accuracy, good balance | 25.9M params, \~3x slower on CPU |

| RT-DETR | Transformer-based, strong accuracy | Requires GPU for real-time inference |

| MediaPipe Pose | Lightweight, pose estimation | Not optimised for counting/tracking |



\### What AI Suggested

Claude suggested YOLOv8m as the starting point, citing its better accuracy on

crowded scenes — directly relevant to the group entry and billing queue edge cases.

It noted that mAP50 on COCO for YOLOv8m (50.2) is meaningfully higher than

YOLOv8n (37.3) for person detection in occluded scenes.



\### What I Chose and Why

\*\*YOLOv8n\*\* with `classes=\[0]` (person-only inference).



I overrode the AI suggestion for three reasons:



1\. \*\*Hardware constraint\*\*: This deployment targets CPU-only machines. YOLOv8m

&#x20;  runs at \~4 FPS on a modern laptop CPU. YOLOv8n runs at \~15 FPS — matching

&#x20;  the clip's native frame rate. Processing at native FPS means no frame

&#x20;  skipping and no temporal gaps in tracking.



2\. \*\*Controlled environment\*\*: Retail CCTV has fixed camera angles, stable

&#x20;  backgrounds, and consistent lighting. This is not a wild-scene detection

&#x20;  problem. YOLOv8n's accuracy is sufficient for this constrained domain.



3\. \*\*`classes=\[0]` filter\*\*: By restricting inference to the person class only,

&#x20;  we eliminate false positives from shopping bags, mannequins, and carts.

&#x20;  This accuracy improvement partially compensates for the smaller model.



\*\*What would make me change this decision\*\*: A GPU-enabled deployment target,

or evidence from the held-out clip that YOLOv8n misses more than 15% of

detections in the billing queue scene (the hardest scene for small models).



\---



\## Decision 2: Re-ID Strategy



\### Options Considered



| Approach | Pros | Cons |

|---|---|---|

| OSNet (torchreid) | State-of-art Re-ID accuracy | 400MB+ model, GPU required, \~1-2 FPS on CPU |

| Color histogram cosine similarity | Microsecond inference, zero extra dependencies | Lower accuracy, sensitive to lighting changes |

| Bounding box trajectory | No appearance model needed | Breaks on occlusion, ID switching |

| DeepSORT appearance | Built-in to tracker | Requires separate Re-ID model weights |



\### What AI Suggested

Claude's first suggestion was OSNet via torchreid, citing its superior accuracy

on the Market-1501 benchmark (mAP 84.9%). It correctly identified that Re-ID

accuracy directly impacts the conversion rate metric's correctness.



\### What I Chose and Why

\*\*Color histogram cosine similarity\*\* on the torso crop region.



I rejected OSNet for this deployment for concrete reasons:



1\. \*\*CPU performance\*\*: OSNet at CPU inference runs at 1-2 FPS. Our pipeline

&#x20;  processes 15 FPS video. Using OSNet would require frame-skipping every

&#x20;  7-8 frames for Re-ID, meaning we only check for re-entry 2 times per second.

&#x20;  A customer who re-enters and walks quickly past the entry camera in under

&#x20;  500ms would be missed entirely.



2\. \*\*Dependency weight\*\*: OSNet adds a 400MB+ model download. The spec says

&#x20;  `docker compose up` must work on a clean machine. A 400MB download inside

&#x20;  the Docker build makes the setup fragile on slow connections.



3\. \*\*Retail-specific insight\*\*: In a retail environment, staff uniforms and

&#x20;  customer clothing are strong discriminative signals. Color histograms

&#x20;  capture this well. The 300-second re-entry window also bounds the problem:

&#x20;  we only compare against visitors who exited in the last 5 minutes, keeping

&#x20;  the comparison pool small and the false-match rate low.



\*\*Accuracy trade-off acknowledged\*\*: Color histograms will fail when two

customers wear similar-colored clothing. This is documented via the

`confidence` field — re-entry matches below `REID\_COSINE\_THRESHOLD=0.65`

are treated as new visitors rather than forced matches.



\*\*What would make me change this decision\*\*: A GPU deployment target, or

a store with a uniform dress code among customers (e.g. a school uniform store).



\---



\## Decision 3: Storage Engine Selection



\### Options Considered



| Engine | Pros | Cons |

|---|---|---|

| SQLite | Zero infrastructure, file-based, ACID | Single-writer, not horizontally scalable |

| PostgreSQL | Production-grade, concurrent writes, extensions | Requires separate container, more setup |

| Redis + PostgreSQL | Redis for real-time counters, PG for history | Complex two-store architecture |

| ClickHouse | Columnar, fast analytics queries | Overkill for this scale, complex setup |



\### What AI Suggested

Claude suggested PostgreSQL as the production choice, noting that SQLite's

single-writer lock would become a bottleneck at 40 stores sending concurrent

events. It specifically flagged the `/events/ingest` endpoint: if 40 stores

send 500-event batches simultaneously, SQLite's write lock creates a queue

of 40 waiting transactions.



\### What I Chose and Why

\*\*SQLite\*\* for this submission, with a documented upgrade path to PostgreSQL.



I chose SQLite for the following reasons:



1\. \*\*Acceptance gate requirement\*\*: The spec says `docker compose up` must

&#x20;  start everything. SQLite requires zero additional containers. PostgreSQL

&#x20;  requires a second service, health checks, and initialization scripts that

&#x20;  add failure points on a clean machine.



2\. \*\*Scale of this challenge\*\*: The dataset is 5 stores × 3 cameras × 20

&#x20;  minutes at 15 FPS. Even at full throughput, this produces \~50,000 events

&#x20;  total. SQLite handles millions of rows comfortably with proper indexing.



3\. \*\*Upgrade path is trivial\*\*: The entire database layer uses SQLAlchemy ORM.

&#x20;  Switching from SQLite to PostgreSQL requires changing one environment

&#x20;  variable: `DATABASE\_URL=postgresql://user:pass@db:5432/store\_intel`.

&#x20;  No application code changes required.



\*\*Where AI was right\*\*: At 40 live stores in production, SQLite's single-writer

lock would be the first thing to break under concurrent ingest load. The correct

production architecture is PostgreSQL with connection pooling (pgBouncer) and

read replicas for the analytics queries. I would make this change before any

production deployment.



\*\*Composite indexes added to mitigate SQLite limitations\*\*:

\- `ix\_store\_time (store\_id, timestamp)` — prevents full table scans on metrics

\- `ix\_visitor\_store (visitor\_id, store\_id)` — prevents full table scans on funnel



Without these indexes, SQLite at 50K+ rows would make the API unusable.



\---



\## Summary: Where I Agreed vs Overrode AI



| Decision | AI Suggestion | My Choice | Outcome |

|---|---|---|---|

| Detection model | YOLOv8m | YOLOv8n | Overrode — CPU performance constraint |

| Re-ID model | OSNet torchreid | Color histogram | Overrode — CPU performance + dependency weight |

| Storage engine | PostgreSQL | SQLite | Overrode — deployment simplicity; upgrade path documented |

| Schema validators | Add cross-field validators | Implemented as suggested | Agreed — caught real bugs in testing |

| Funnel stage capping | Cap each stage at previous | Implemented as suggested | Agreed — prevents impossible funnel shapes from camera overlap |

