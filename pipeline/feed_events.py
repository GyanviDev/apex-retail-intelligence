"""
Feed generated events from .jsonl files into the Intelligence API.
Usage:
    python pipeline/feed_events.py --store ST1008 --api http://localhost:8000
"""

import json
import urllib.request
import urllib.error
import argparse
import os
import glob
import time


def send_batch(api_base: str, events: list) -> dict:
    """Send a batch of events to the ingest endpoint."""
    data = json.dumps({"events": events}).encode("utf-8")
    req  = urllib.request.Request(
        f"{api_base}/events/ingest",
        data    = data,
        headers = {"Content-Type": "application/json"},
        method  = "POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": str(e), "accepted": 0, "rejected": 0, "duplicate": 0}


def feed_file(api_base: str, jsonl_path: str, batch_size: int = 500):
    """Read a .jsonl file and feed events in batches."""
    print(f"\n[INFO] Processing: {jsonl_path}")

    with open(jsonl_path, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]

    events = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not events:
        print(f"[WARN] No valid events in {jsonl_path}")
        return

    total     = len(events)
    accepted  = 0
    rejected  = 0
    duplicate = 0

    for i in range(0, total, batch_size):
        batch  = events[i:i + batch_size]
        result = send_batch(api_base, batch)
        accepted  += result.get("accepted",  0)
        rejected  += result.get("rejected",  0)
        duplicate += result.get("duplicate", 0)

        pct = min(100, (i + len(batch)) / total * 100)
        print(f"  [{pct:5.1f}%] Sent {i+len(batch)}/{total} | "
              f"accepted={accepted} rejected={rejected} dup={duplicate}")

    print(f"[DONE] {jsonl_path}: "
          f"total={total} accepted={accepted} "
          f"rejected={rejected} duplicate={duplicate}")


def main():
    parser = argparse.ArgumentParser(
        description="Feed generated events into the Intelligence API"
    )
    parser.add_argument(
        "--store",
        default = "ST1008",
        help    = "Store ID to filter event files",
    )
    parser.add_argument(
        "--api",
        default = "http://localhost:8000",
        help    = "API base URL",
    )
    parser.add_argument(
        "--events-dir",
        default = "data/events",
        help    = "Directory containing .jsonl event files",
    )
    parser.add_argument(
        "--batch-size",
        type    = int,
        default = 500,
        help    = "Events per API call",
    )
    args = parser.parse_args()

    # Find all event files for this store
    pattern = os.path.join(args.events_dir, f"{args.store}_*.jsonl")
    files   = sorted(glob.glob(pattern))

    if not files:
        print(f"[WARN] No event files found matching: {pattern}")
        print(f"       Run pipeline/run.sh first to generate events.")
        return

    print(f"[INFO] Found {len(files)} event file(s) for {args.store}")
    print(f"[INFO] API: {args.api}")

    # Verify API is reachable — accept 503 (no data yet) as healthy
    # Only abort if connection is refused or times out entirely
    try:
        with urllib.request.urlopen(
            f"{args.api}/health", timeout=5
        ) as resp:
            health = json.loads(resp.read().decode())
            print(f"[INFO] API status: {health.get('status', 'UNKNOWN')}")
    except urllib.error.HTTPError as e:
        if e.code == 503:
            # 503 means API is running but no store data yet — this is fine
            print(f"[INFO] API is running (no store data yet — will ingest now)")
        else:
            print(f"[ERROR] API returned unexpected error: {e}")
            return
    except Exception as e:
        print(f"[ERROR] API not reachable at {args.api}: {e}")
        return

    start = time.time()
    for f in files:
        feed_file(args.api, f, args.batch_size)

    elapsed = time.time() - start
    print(f"\n[INFO] All files processed in {elapsed:.1f}s")
    print(f"[INFO] Check metrics: {args.api}/stores/{args.store}/metrics")


if __name__ == "__main__":
    main()