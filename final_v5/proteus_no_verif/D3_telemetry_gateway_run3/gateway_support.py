"""Support types for gateway.TelemetryGateway.

Groups the estimator construction parameters into a value object and
centralises the "get-or-create" bookkeeping that was previously inline in
TelemetryGateway._estimator.
"""
from dataclasses import dataclass, asdict

from estimator import Estimator


@dataclass
class EstimatorConfig:
    scale: float = 0.1
    window: int = 5
    roc_limit: float = 5.0

    def as_dict(self) -> dict:
        return asdict(self)

    def build(self) -> Estimator:
        return Estimator(**self.as_dict())


class EstimatorRegistry:
    """Owns the per-tag Estimator instances and the shared config used
    to create new ones on demand."""

    def __init__(self, config: EstimatorConfig, store: dict = None):
        self.config = config
        self.store = store if store is not None else {}

    def get(self, tag: str) -> Estimator:
        if tag not in self.store:
            self.store[tag] = self.config.build()
        return self.store[tag]
