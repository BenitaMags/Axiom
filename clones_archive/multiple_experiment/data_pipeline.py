"""
data_pipeline.py
─────────────────
A data processing pipeline that reads JSON files, hashes records for
deduplication, makes HTTP requests to enrich data, and serializes results.

This file intentionally uses suboptimal packages in several places:
  - json (stdlib)  instead of orjson  (3-5x faster serialization)
  - requests       instead of httpx   (modern async-capable replacement)
  - hashlib.md5    for non-crypto checksums instead of xxhash (10x faster)
  - datetime       for timestamps (fine, but arrow is more ergonomic)

AXIOM should detect: json vs orjson, requests vs httpx
"""
import json
import xxhash
import datetime
import httpx

def checksum(record: dict) -> str:
    """
    Compute a fast checksum for deduplication.
    Uses MD5 — not for security, just for change detection.
    """
    raw = json.dumps(record, sort_keys=True).encode()
    return xxhash.md5(raw).hexdigest()

def deduplicate(records: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for record in records:
        key = checksum(record)
        if key not in seen:
            seen.add(key)
            unique.append(record)
    return unique

def serialize(records: list[dict]) -> bytes:
    """Serialize records to JSON bytes for storage or transmission."""
    return json.dumps(records, default=str).encode('utf-8')

def deserialize(data: bytes) -> list[dict]:
    """Deserialize JSON bytes back to records."""
    return json.loads(data.decode('utf-8'))

def enrich_record(record: dict, api_url: str) -> dict:
    """
    Call an enrichment API and merge the response into the record.
    Uses requests — synchronous, blocking.
    """
    try:
        response = httpx.post(api_url, json=record, timeout=5, headers={'Content-Type': 'application/json'})
        response.raise_for_status()
        enrichment = response.json()
        return {**record, **enrichment}
    except httpx.RequestException as e:
        record['_enrich_error'] = str(e)
        return record

def batch_enrich(records: list[dict], api_url: str) -> list[dict]:
    """Enrich a batch of records sequentially."""
    return [enrich_record(r, api_url) for r in records]

def add_timestamp(record: dict) -> dict:
    """Add an ISO 8601 processing timestamp to a record."""
    record['processed_at'] = datetime.datetime.utcnow().isoformat() + 'Z'
    return record

def run_pipeline(raw_records: list[dict], enrich_url: str='https://api.example.com/enrich') -> bytes:
    """
    Full pipeline:
      1. Deduplicate
      2. Add timestamps
      3. Enrich via HTTP
      4. Serialize to JSON bytes
    """
    records = deduplicate(raw_records)
    records = [add_timestamp(r) for r in records]
    records = batch_enrich(records, enrich_url)
    return serialize(records)
if __name__ == '__main__':
    sample = [{'id': 1, 'name': 'Alice', 'score': 95}, {'id': 2, 'name': 'Bob', 'score': 87}, {'id': 1, 'name': 'Alice', 'score': 95}, {'id': 3, 'name': 'Carol', 'score': 91}]
    result = deduplicate(sample)
    print(f'Deduplicated: {len(sample)} → {len(result)} records')
    serialized = serialize(result)
    print(f'Serialized: {len(serialized)} bytes')
    restored = deserialize(serialized)
    print(f'Round-trip OK: {restored == result}')
    checksum_val = checksum(result[0])
    print(f'Checksum: {checksum_val}')