import json
from pathlib import Path
from typing import Any


class JsonCache:
    def __init__(self, cache_dir: str | Path = ".cache") -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def save(self, key: str, data: Any) -> None:
        path = self.cache_dir / f"{key}.json"
        path.write_text(json.dumps(data, indent=2, default=str))

    def load(self, key: str) -> Any | None:
        path = self.cache_dir / f"{key}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def invalidate(self, key: str) -> None:
        (self.cache_dir / f"{key}.json").unlink(missing_ok=True)

    def serialize(self, data: Any) -> str:
        return json.dumps(data, default=str)

    def deserialize(self, raw: str) -> Any:
        return json.loads(raw)
