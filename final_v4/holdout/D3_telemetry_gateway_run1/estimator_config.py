"""estimator_config -- value object bundling estimator tuning parameters."""
from dataclasses import dataclass, asdict


@dataclass
class EstimatorConfig:
    scale: float = 0.1
    window: int = 5
    roc_limit: float = 5.0

    def as_dict(self) -> dict:
        return asdict(self)
