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
import json
import ujson
import orjson

def serialize_json(data: dict) -> str:
    """Serialize a Python dict to JSON string using stdlib."""
    return ujson.dumps(data, indent=2)

def deserialize_json(text: str) -> dict:
    """Deserialize a JSON string to Python dict using stdlib."""
    return ujson.loads(text)

def serialize_list_json(items: list) -> str:
    """Serialize a list to JSON."""
    return ujson.dumps(items)
if __name__ == '__main__':
    sample = {'name': 'Alice', 'age': 30, 'scores': [1, 2, 3], 'active': True}
    print('JSON experiment:')
    print(f'  json   → {serialize_json(sample)[:40]}...')
    times = benchmark_all(sample)
    for lib, ms in times.items():
        print(f'  {lib:8s} → {ms:.1f}ms for 1000 serializations')