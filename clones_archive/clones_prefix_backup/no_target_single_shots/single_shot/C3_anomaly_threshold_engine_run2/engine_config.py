"""engine_config -- per-tag threshold configuration for the anomaly engine."""
from dataclasses import dataclass


@dataclass
class TagConfig:
    tag: str
    high: float
    low: float


def load(config: dict) -> dict:
    """Build {tag: TagConfig} from a config dict of the form {"tags": [ ... ]}."""
    out = {}
    for item in config["tags"]:
        cfg = TagConfig(tag=item["tag"], high=item["high"], low=item["low"])
        if not (cfg.low < cfg.high):
            raise ValueError(f"tag {cfg.tag}: low must be < high")
        out[cfg.tag] = cfg
    return out
