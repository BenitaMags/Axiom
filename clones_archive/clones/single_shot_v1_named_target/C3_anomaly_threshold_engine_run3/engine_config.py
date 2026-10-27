"""engine_config -- per-tag threshold configuration for the anomaly engine."""
from dataclasses import dataclass


@dataclass
class TagConfig:
    tag: str
    high: float
    low: float


def load(config: dict) -> dict:
    """Build {tag: TagConfig} from a config dict of the form {"tags": [ ... ]}."""
    output = {}
    entries = config["tags"]
    idx = 0
    while idx < len(entries):
        entry = entries[idx]
        candidate = TagConfig(tag=entry["tag"], high=entry["high"], low=entry["low"])
        if not (candidate.low < candidate.high):
            raise ValueError(f"tag {candidate.tag}: low must be < high")
        output[candidate.tag] = candidate
        idx += 1
    return output
