"""
experiment_libs/json_experiment.py
────────────────────────────────────
Experiment file: JSON serializers

Tests AXIOM's ability to detect that json, ujson, and orjson
all serve the same role (JSON serialization) and pick the fastest.

Packages involved:
  - json    : Python stdlib, always available, slowest (~3ms import)
  - ujson   : UltraJSON, C extension, 2-3x faster serialization
  - orjson  : Rust-based, fastest JSON library for Python (~10ms import)

AXIOM should detect: json ≡ ujson ≡ orjson (same role)
Expected winner: orjson or ujson (faster import + faster serialization)
"""
import ujson
try:
    import ujson
except ImportError:
    ujson = None
try:
    import orjson
except ImportError:
    orjson = None

def serialize_json(data: dict) -> str:
    """Serialize a Python dict to JSON string using stdlib."""
    return json.dumps(data, indent=2)

def deserialize_json(text: str) -> dict:
    """Deserialize a JSON string to Python dict using stdlib."""
    return json.loads(text)

def serialize_list_json(items: list) -> str:
    """Serialize a list to JSON."""
    return json.dumps(items)

def serialize_ujson(data: dict) -> str:
    """Faster serialization using ujson."""
    if ujson is None:
        return json.dumps(data)
    return ujson.dumps(data)

def deserialize_ujson(text: str) -> dict:
    """Faster deserialization using ujson."""
    if ujson is None:
        return json.loads(text)
    return ujson.loads(text)

def serialize_orjson(data: dict) -> bytes:
    """Fastest serialization using orjson (returns bytes)."""
    if orjson is None:
        return json.dumps(data).encode()
    return orjson.dumps(data)

def deserialize_orjson(data: bytes) -> dict:
    """Fastest deserialization using orjson."""
    if orjson is None:
        return json.loads(data)
    return orjson.loads(data)

def benchmark_all(data: dict, iterations: int=1000) -> dict:
    """Compare serialization speed across all three libraries."""
    import time
    results = {}
    t0 = time.perf_counter()
    for _ in range(iterations):
        json.dumps(data)
    results['json'] = (time.perf_counter() - t0) * 1000
    if ujson:
        t0 = time.perf_counter()
        for _ in range(iterations):
            ujson.dumps(data)
        results['ujson'] = (time.perf_counter() - t0) * 1000
    if orjson:
        t0 = time.perf_counter()
        for _ in range(iterations):
            orjson.dumps(data)
        results['orjson'] = (time.perf_counter() - t0) * 1000
    return results
if __name__ == '__main__':
    sample = {'name': 'Alice', 'age': 30, 'scores': [1, 2, 3], 'active': True}
    print('JSON experiment:')
    print(f'  json   → {serialize_json(sample)[:40]}...')
    times = benchmark_all(sample)
    for lib, ms in times.items():
        print(f'  {lib:8s} → {ms:.1f}ms for 1000 serializations')