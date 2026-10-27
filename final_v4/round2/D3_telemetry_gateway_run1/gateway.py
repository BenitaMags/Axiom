"""gateway -- telemetry gateway: read raw counts, estimate, publish.

Combines all three transformations in one program:
  * LSA + GSA: swap the MQTT client (`paho-mqtt` -> `aiomqtt`) and write async glue.
  * LMA: rewrite the first-party estimation/anomaly logic in `estimator.py`.

`reader` is an injected callable `tag -> raw_counts` (the field acquisition source).
"""
from estimator_factory import EstimatorFactory
from sink import MqttSink


class TelemetryGateway:
    def __init__(self, reader, sink: MqttSink, estimators: dict = None,
                 scale: float = 0.1, window: int = 5, roc_limit: float = 5.0):
        self.reader = reader
        self.sink = sink
        self.estimators = estimators if estimators is not None else {}
        self._cfg = dict(scale=scale, window=window, roc_limit=roc_limit)
        self._factory = EstimatorFactory(self._cfg)

    def _estimator(self, tag: str):
        return self._factory.get_or_create(self.estimators, tag)

    def process(self, tag: str) -> dict:
        """Acquire one sample for *tag*, estimate, publish, and return the record."""
        pipeline = (self._acquire, self._estimate, self._tag_record, self._publish)
        ctx = {"tag": tag}
        for stage in pipeline:
            ctx = stage(ctx)
        return ctx["record"]

    def _acquire(self, ctx: dict) -> dict:
        ctx["raw"] = self.reader(ctx["tag"])
        return ctx

    def _estimate(self, ctx: dict) -> dict:
        ctx["record"] = self._estimator(ctx["tag"]).update(ctx["raw"])
        return ctx

    def _tag_record(self, ctx: dict) -> dict:
        ctx["record"]["tag"] = ctx["tag"]
        return ctx

    def _publish(self, ctx: dict) -> dict:
        self.sink.publish(ctx["tag"], ctx["record"])
        return ctx

    def process_all(self, tags) -> list:
        return [self.process(t) for t in tags]
