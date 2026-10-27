"""gateway -- telemetry gateway: read raw counts, estimate, publish.

Combines all three transformations in one program:
  * LSA + GSA: swap the MQTT client (`paho-mqtt` -> `aiomqtt`) and write async glue.
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
        self._cfg = {"scale": scale, "window": window, "roc_limit": roc_limit}

    def _estimator(self, tag: str) -> Estimator:
        found = self.estimators.get(tag)
        if found is None:
            found = Estimator(**self._cfg)
            self.estimators[tag] = found
        return found

    def process(self, tag: str) -> dict:
        """Acquire one sample for *tag*, estimate, publish, and return the record."""
        est = self._estimator(tag)
        raw_counts = self.reader(tag)
        record = est.update(raw_counts)
        record.update(tag=tag)
        self.sink.publish(tag, record)
        return record

    def process_all(self, tags) -> list:
        results = []
        for t in tags:
            results.append(self.process(t))
        return results
