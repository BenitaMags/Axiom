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


def _build_tag_config(entry: dict) -> TagConfig:
    return TagConfig(tag=entry["tag"], high=entry["high"], low=entry["low"]).validate()


def load(config: dict) -> dict:
    """Build {tag: TagConfig} from a config dict of the form {"tags": [ ... ]}."""
    tag_configs = (_build_tag_config(entry) for entry in config["tags"])
    return {tc.tag: tc for tc in tag_configs}
