"""
Inventory Sync Pipeline
=======================
Reads product inventory from CSV, deduplicates, caches to JSON,
fingerprints the dataset, and posts a summary report to a webhook.

Run AXIOM on this file to identify which packages can be swapped for
faster alternatives and whether a compatibility shim is needed:

    cd experiment && axiom main.py

Expected AXIOM findings
-----------------------
Package   Suggested alt   Connector needed?   Reason
-------   -------------   -----------------   ------
csv       polars          YES                 row-iteration vs DataFrame API — completely different
json      orjson          YES                 orjson.dumps() returns bytes, not str
hashlib   xxhash          YES                 different function names (md5 → xxh64, sha256 → xxh3)
requests  httpx           NO                  .get() .post() .json() .raise_for_status() are identical
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
import time
from pathlib import Path

import requests

# Make sibling modules importable when running as: python main.py  or  axiom main.py
sys.path.insert(0, str(Path(__file__).parent))

from modules.csv_reader    import CsvReader
from modules.deduplicator  import Deduplicator
from modules.http_reporter import HttpReporter
from modules.json_cache    import JsonCache

# ── Config ─────────────────────────────────────────────────────────────────────

INVENTORY_CSV = Path(__file__).parent / "data" / "inventory.csv"
CATEGORY_CSV  = Path(__file__).parent / "data" / "category_config.csv"
CACHE_DIR     = Path(__file__).parent / ".cache"
WEBHOOK_BASE  = "https://httpbin.org"


# ── Step 1: Load category metadata (direct csv usage) ─────────────────────────
# AXIOM will see: csv.DictReader — candidate for polars.read_csv()
# Connector needed: yes — polars returns a DataFrame, not an iterator of dicts

def load_category_map(path: Path) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            code = row.pop("code")
            result[code] = dict(row)
    return result


# ── Step 2: Build aggregated summary ──────────────────────────────────────────

def build_summary(
    records: list[dict[str, str]],
    category_map: dict[str, dict[str, str]],
) -> dict:
    value_by_cat: dict[str, float] = {}
    count_by_cat: dict[str, int]   = {}

    for rec in records:
        cat   = rec.get("category", "Unknown")
        price = float(rec.get("price", 0.0))
        value_by_cat[cat] = round(value_by_cat.get(cat, 0.0) + price, 2)
        count_by_cat[cat] = count_by_cat.get(cat, 0) + 1

    return {
        "record_count": len(records),
        "by_category": {
            cat: {
                "count":    count_by_cat[cat],
                "value":    value_by_cat[cat],
                "label":    category_map.get(cat, {}).get("label", cat),
                "tax_rate": float(category_map.get(cat, {}).get("tax_rate", 0.0)),
            }
            for cat in sorted(count_by_cat)
        },
    }


# ── Step 3: Dataset fingerprint (direct hashlib usage) ────────────────────────
# AXIOM will see: hashlib.sha256 — candidate for xxhash.xxh3_128
# Connector needed: yes — xxhash uses xxhash.xxh3_128(data).hexdigest(),
#                          not hashlib.sha256(data).hexdigest()

def fingerprint(records: list[dict]) -> str:
    blob = json.dumps(records, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


# ── Step 4: Inline JSON roundtrip (direct json usage) ─────────────────────────
# AXIOM will see: json.dumps / json.loads — candidate for orjson
# Connector needed: yes — orjson.dumps() returns bytes, json.dumps() returns str
#                         call sites doing  s = json.dumps(x)  then  s.upper()
#                         would break because bytes has no .upper()

def verify_roundtrip(data: dict) -> None:
    serialised  = json.dumps(data, default=str)
    deserialised = json.loads(serialised)
    assert deserialised["record_count"] == data["record_count"], "Roundtrip mismatch"


# ── Step 5: HTTP health-check (direct requests usage) ─────────────────────────
# AXIOM will see: requests.get — candidate for httpx
# Connector needed: no — httpx.get() / .post() / .json() / .raise_for_status()
#                        are identical to requests; it is a true drop-in

def ping_webhook(base_url: str) -> bool:
    try:
        resp = requests.get(f"{base_url}/get", timeout=5)
        resp.raise_for_status()
        return resp.json().get("url") is not None
    except requests.RequestException:
        return False


# ── Pipeline ───────────────────────────────────────────────────────────────────

def main() -> None:
    t0 = time.perf_counter()

    # 1. Category config — direct csv.DictReader
    category_map = load_category_map(CATEGORY_CSV)
    print(f"[1/7] Loaded {len(category_map)} category definitions")

    # 2. Inventory rows — via CsvReader module (uses csv internally)
    reader = CsvReader(INVENTORY_CSV)
    raw    = reader.read_all()
    print(f"[2/7] Read {len(raw)} inventory rows ({reader.count()} via count())")

    # 3. Deduplicate — via Deduplicator module (uses hashlib.md5 internally)
    dedup   = Deduplicator()
    unique  = dedup.filter(raw)
    dropped = len(raw) - len(unique)
    print(f"[3/7] Deduplicated: {len(unique)} unique, {dropped} duplicates removed")

    # 4. Dataset fingerprint — direct hashlib.sha256
    fp = fingerprint(unique)
    print(f"[4/7] Dataset fingerprint: {fp[:20]}…")

    # 5. Build summary
    summary = build_summary(unique, category_map)
    print(f"[5/7] Summary built: {summary['record_count']} records across "
          f"{len(summary['by_category'])} categories")

    # 6. JSON verify + cache — direct json.dumps/loads, then JsonCache (uses json internally)
    verify_roundtrip(summary)
    cache = JsonCache(CACHE_DIR)
    cache.save("latest_summary", summary)
    cached = cache.load("latest_summary")
    assert cached is not None, "Cache read failed"
    print(f"[6/7] Cached & verified → {CACHE_DIR / 'latest_summary.json'}")

    # 7. HTTP report — direct requests.get health-check, then HttpReporter (uses requests internally)
    alive = ping_webhook(WEBHOOK_BASE)
    if alive:
        with HttpReporter(WEBHOOK_BASE) as reporter:
            response = reporter.post("/post", {
                "fingerprint": fp,
                "summary":     summary,
            })
        print(f"[7/7] Report posted → status confirmed via {response.get('url', WEBHOOK_BASE)}")
    else:
        print("[7/7] Webhook unreachable — report skipped (network may be offline)")

    elapsed = time.perf_counter() - t0
    print(f"\nDone in {elapsed:.3f}s")
    print("\nRun `axiom main.py` to see which packages AXIOM recommends replacing.")


if __name__ == "__main__":
    main()
