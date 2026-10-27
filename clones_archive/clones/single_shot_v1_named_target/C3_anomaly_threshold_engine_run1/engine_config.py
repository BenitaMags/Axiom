"""engine_config -- per-tag threshold configuration for the anomaly engine."""
from dataclasses import dataclass


@dataclass
class TagConfig:
    tag: str
    high: float
    low: float


def load(config: dict) -> dict:
    """Build {tag: TagConfig} from a config dict of the form {"tags": [ ... ]}."""
    tags_by_name = {}
    for entry in config["tags"]:
        current = TagConfig(tag=entry["tag"], high=entry["high"], low=entry["low"])
        if not (current.low < current.high):
            raise ValueError(f"tag {current.tag}: low must be < high")
        tags_by_name[current.tag] = current
    return tags_by_name
