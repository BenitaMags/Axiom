"""engine_config -- per-tag threshold configuration for the anomaly engine."""
from dataclasses import dataclass


@dataclass
class TagConfig:
    tag: str
    high: float
    low: float


def load(config: dict) -> dict:
    """Build {tag: TagConfig} from a config dict of the form {"tags": [ ... ]}."""
    result = {}
    for entry in config["tags"]:
        tc = TagConfig(tag=entry["tag"], high=entry["high"], low=entry["low"])
        if tc.low >= tc.high:
            raise ValueError(f"tag {tc.tag}: low must be < high")
        result[tc.tag] = tc
    return result
