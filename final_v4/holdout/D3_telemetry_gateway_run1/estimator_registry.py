"""estimator_registry -- lazily-created, per-tag Estimator instances."""
from estimator import Estimator
from estimator_config import EstimatorConfig


class EstimatorRegistry:
    def __init__(self, config: EstimatorConfig, initial: dict = None):
        self.config = config
        self.store = initial if initial is not None else {}

    def get(self, tag: str) -> Estimator:
        if tag not in self.store:
            self.store[tag] = Estimator(**self.config.as_dict())
        return self.store[tag]
