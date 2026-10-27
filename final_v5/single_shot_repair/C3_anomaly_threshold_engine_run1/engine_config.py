"""engine_config -- per-tag threshold configuration for the anomaly engine."""
from dataclasses import dataclass


@dataclass
class TagConfig:
    tag: str
    high: float
    low: float

    def validate(self) -> "TagConfig":
        if self.low >= self.high:
            raise ValueError(f"tag {self.tag}: low must be < high")
        return self


def _to_tag_config(entry: dict) -> TagConfig:
    return TagConfig(tag=entry["tag"], high=entry["high"], low=entry["low"]).validate()


def load(config: dict) -> dict:
    """Build {tag: TagConfig} from a config dict of the form {"tags": [ ... ]}."""
    return {tc.tag: tc for tc in (_to_tag_config(entry) for entry in config["tags"])}
