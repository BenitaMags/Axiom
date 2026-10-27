"""gateway -- telemetry gateway: read raw counts, estimate, publish.

Combines all three transformations in one program:
  * LSA + GSA: swap the MQTT client (`paho-mqtt` -> `aiomqtt`) and write async glue.
  * LMA: rewrite the first-party estimation/anomaly logic in `estimator.py`.

`reader` is an injected callable `tag -> raw_counts` (the field acquisition source).
"""
from estimator_config import EstimatorConfig
from estimator_registry import EstimatorRegistry
from sink import MqttSink


class TelemetryGateway:
    def __init__(self, reader, sink: MqttSink, estimators: dict = None,
                 scale: float = 0.1, window: int = 5, roc_limit: float = 5.0):
        self.reader = reader
        self.sink = sink
        self._registry = EstimatorRegistry(
            EstimatorConfig(scale=scale, window=window, roc_limit=roc_limit),
            initial=estimators,
        )

    @property
    def estimators(self) -> dict:
        return self._registry.store

    @estimators.setter
    def estimators(self, value: dict) -> None:
        self._registry.store = value

    @property
    def _cfg(self) -> dict:
        return self._registry.config.as_dict()

    @_cfg.setter
    def _cfg(self, value: dict) -> None:
        self._registry.config = EstimatorConfig(**value)

    def _estimator(self, tag: str):
        return self._registry.get(tag)

    def _build_record(self, tag: str) -> dict:
        raw = self.reader(tag)
        record = self._estimator(tag).update(raw)
        record["tag"] = tag
        return record

    def process(self, tag: str) -> dict:
        """Acquire one sample for *tag*, estimate, publish, and return the record."""
        record = self._build_record(tag)
        self.sink.publish(tag, record)
        return record

    def process_all(self, tags) -> list:
        return [self.process(t) for t in tags]
