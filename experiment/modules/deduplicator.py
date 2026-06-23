import hashlib
from typing import Any


def _record_hash(record: dict[str, Any]) -> str:
    payload = ",".join(f"{k}={v}" for k, v in sorted(record.items()))
    return hashlib.md5(payload.encode()).hexdigest()


class Deduplicator:
    def __init__(self) -> None:
        self._seen: set[str] = set()

    def is_duplicate(self, record: dict[str, Any]) -> bool:
        h = _record_hash(record)
        if h in self._seen:
            return True
        self._seen.add(h)
        return False

    def filter(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self._seen.clear()
        return [r for r in records if not self.is_duplicate(r)]

    @property
    def seen_count(self) -> int:
        return len(self._seen)
