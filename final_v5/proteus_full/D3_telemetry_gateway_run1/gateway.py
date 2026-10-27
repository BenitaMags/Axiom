"""gateway -- telemetry gateway: read raw counts, estimate, publish.

Combines all three transformations in one program:
  * LSA + GSA: swap the MQTT client (`paho-mqtt` -> `aiomqtt`) and write async glue.
  * LMA: rewrite the first-party estimation/anomaly logic in `estimator.py`.

`reader` is an injected callable `tag -> raw_counts` (the field acquisition source).
"""
from estimator import Estimator
from sink import MqttSink

from estimator_config import EstimatorConfig


class TelemetryGateway:
    def __init__(self, reader, sink: MqttSink, estimators: dict = None,
                 scale: float = 0.1, window: int = 5, roc_limit: float = 5.0):
        self.reader = reader
        self.sink = sink
        self.estimators = estimators if estimators is not None else {}
        self._config = EstimatorConfig(scale=scale, window=window, roc_limit=roc_limit)

    @property
    def _cfg(self) -> dict:
        return self._config.as_dict()

    @_cfg.setter
    def _cfg(self, value: dict):
        self._config = EstimatorConfig(**value)

    def _get_or_create_estimator(self, tag: str) -> Estimator:
        existing = self.estimators.get(tag)
        if existing is not None:
            return existing
        created = self._config.build_estimator()
        self.estimators[tag] = created
        return created

    def _estimator(self, tag: str) -> Estimator:
        return self._get_or_create_estimator(tag)

    def _publish_record(self, tag: str, record: dict) -> dict:
        record["tag"] = tag
        self.sink.publish(tag, record)
        return record

    def process(self, tag: str) -> dict:
        """Acquire one sample for *tag*, estimate, publish, and return the record."""
        raw = self.reader(tag)
        record = self._estimator(tag).update(raw)
        return self._publish_record(tag, record)

    def process_all(self, tags) -> list:
        return [self.process(t) for t in tags]
