"""engine_config -- per-tag threshold configuration for the anomaly engine."""
from dataclasses import dataclass


@dataclass
class TagConfig:
    tag: str
    high: float
    low: float

    def __post_init__(self):
        if self.low >= self.high:
            raise ValueError(f"tag {self.tag}: low must be < high")


def load(config: dict) -> dict:
    """Build {tag: TagConfig} from a config dict of the form {"tags": [ ... ]}."""
    entries = (
        TagConfig(tag=entry["tag"], high=entry["high"], low=entry["low"])
        for entry in config["tags"]
    )
    return {tc.tag: tc for tc in entries}
