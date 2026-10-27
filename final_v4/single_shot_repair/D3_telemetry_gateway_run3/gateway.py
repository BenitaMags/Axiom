"""gateway -- telemetry gateway: read raw counts, estimate, publish.

Combines all three transformations in one program:

  * LMA: rewrite the first-party estimation/anomaly logic in `estimator.py`.

`reader` is an injected callable `tag -> raw_counts` (the field acquisition source).
"""
from estimator import Estimator
from sink import MqttSink


class TelemetryGateway:
    def __init__(self, reader, sink: MqttSink, estimators: dict = None,
                 scale: float = 0.1, window: int = 5, roc_limit: float = 5.0):
        self.reader = reader
        self.sink = sink
        self.estimators = estimators if estimators is not None else {}
        self._cfg = dict(scale=scale, window=window, roc_limit=roc_limit)

    def _estimator(self, tag: str) -> Estimator:
        if tag not in self.estimators:
            self.estimators[tag] = Estimator(**self._cfg)
        return self.estimators[tag]

    def _build_record(self, tag: str, raw: float) -> dict:
        estimate = self._estimator(tag).update(raw)
        estimate["tag"] = tag
        return estimate

    def process(self, tag: str) -> dict:
        """Acquire one sample for *tag*, estimate, publish, and return the record."""
        raw = self.reader(tag)
        record = self._build_record(tag, raw)
        self.sink.publish(tag, record)
        return record

    def process_all(self, tags) -> list:
        results = []
        for t in tags:
            results.append(self.process(t))
        return results
