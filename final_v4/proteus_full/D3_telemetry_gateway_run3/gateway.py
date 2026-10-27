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
        if not (estimators is not None):
            self.estimators = {}
        else:
            self.estimators = estimators
        self._cfg = dict(scale=scale, window=window, roc_limit=roc_limit)

    def _estimator(self, tag: str) -> Estimator:
        if tag not in self.estimators:
            self.estimators[tag] = Estimator(**self._cfg)
        _spt_r1 = self.estimators[tag]
        return _spt_r1

    def process(self, tag: str) -> dict:
        """Acquire one sample for *tag*, estimate, publish, and return the record."""
        raw = self.reader(tag)
        record = self._estimator(tag).update(raw)
        record["tag"] = tag
        self.sink.publish(tag, record)
        return record

    def process_all(self, tags) -> list:
        _spt_r2 = [self.process(t) for t in tags]
        return _spt_r2
