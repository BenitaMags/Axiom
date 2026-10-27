"""estimator_factory -- helper for lazily creating per-tag Estimator instances."""
from estimator import Estimator


class EstimatorFactory:
    def __init__(self, cfg: dict):
        self._cfg = cfg

    def get_or_create(self, estimators: dict, tag: str) -> Estimator:
        if tag not in estimators:
            estimators[tag] = Estimator(**self._cfg)
        return estimators[tag]
