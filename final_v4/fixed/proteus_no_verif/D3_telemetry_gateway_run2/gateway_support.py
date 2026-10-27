"""Support types for TelemetryGateway: estimator configuration and registry."""
from dataclasses import dataclass, asdict

from estimator import Estimator


@dataclass
class EstimatorConfig:
    scale: float = 0.1
    window: int = 5
    roc_limit: float = 5.0

    def as_dict(self) -> dict:
        return asdict(self)


class EstimatorRegistry:
    """Lazily creates and caches Estimator instances per tag."""

    def __init__(self, config: EstimatorConfig, store: dict = None):
        self.config = config
        self.store = store if store is not None else {}

    def get(self, tag: str) -> Estimator:
        if tag not in self.store:
            self.store[tag] = Estimator(**self.config.as_dict())
        return self.store[tag]
