"""engine_config -- per-tag threshold configuration for the anomaly engine."""
from dataclasses import dataclass

from tag_validation import ensure_bounds_ordered


@dataclass
class TagConfig:
    tag: str
    high: float
    low: float


def _build_tag_config(entry: dict) -> TagConfig:
    tc = TagConfig(tag=entry["tag"], high=entry["high"], low=entry["low"])
    ensure_bounds_ordered(tc.tag, tc.low, tc.high)
    return tc


def load(config: dict) -> dict:
    """Build {tag: TagConfig} from a config dict of the form {"tags": [ ... ]}."""
    return {
        tc.tag: tc
        for tc in (_build_tag_config(entry) for entry in config["tags"])
    }
