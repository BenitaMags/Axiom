"""telemetry -- topic construction and payload encoding for telemetry messages."""
import json


def topic_for(base_topic: str, tag: str) -> str:
    return f"{base_topic}/{tag}"


def encode(reading: dict) -> bytes:
    """Encode a reading into a compact JSON payload (bytes)."""
    body = {
        "value": reading["value"],
        "ts": reading["ts"],
        "q": reading.get("quality", "GOOD"),
    }
    return json.dumps(body, separators=(",", ":")).encode("utf-8")
