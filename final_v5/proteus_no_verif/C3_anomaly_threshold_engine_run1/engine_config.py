"""engine_config -- per-tag threshold configuration for the anomaly engine."""
from dataclasses import dataclass

from tag_config_builder import build_tag_configs


@dataclass
class TagConfig:
    tag: str
    high: float
    low: float


def load(config: dict) -> dict:
    """Build {tag: TagConfig} from a config dict of the form {"tags": [ ... ]}."""
    return build_tag_configs(config, TagConfig)
