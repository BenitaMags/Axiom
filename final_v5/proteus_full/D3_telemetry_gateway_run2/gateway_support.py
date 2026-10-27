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
    """Lazily builds and caches one Estimator per tag."""

    def __init__(self, config: EstimatorConfig, estimators: dict = None):
        self.config = config
        self.estimators = estimators if estimators is not None else {}

    def get(self, tag: str) -> Estimator:
        if tag not in self.estimators:
            self.estimators[tag] = Estimator(**self.config.as_dict())
        return self.estimators[tag]
