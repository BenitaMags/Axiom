"""engine_config -- per-tag threshold configuration for the anomaly engine."""
from dataclasses import dataclass

from tag_config_utils import validate_bounds


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
        validate_bounds(tc)
        result[tc.tag] = tc
    return result
