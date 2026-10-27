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
        _t0 = entry["tag"]
        _t1 = entry["high"]
        _t2 = entry["low"]
        tc = TagConfig(tag=_t0, high=_t1, low=_t2)
        if tc.low >= tc.high:
            raise ValueError(f"tag {tc.tag}: low must be < high")
        result[tc.tag] = tc
    return result
