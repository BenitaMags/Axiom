"""estimator_config -- value object bundling estimator construction parameters."""
from dataclasses import dataclass, asdict

from estimator import Estimator


@dataclass
class EstimatorConfig:
    scale: float
    window: int
    roc_limit: float

    def as_dict(self) -> dict:
        return asdict(self)

    def build_estimator(self) -> Estimator:
        return Estimator(**self.as_dict())
