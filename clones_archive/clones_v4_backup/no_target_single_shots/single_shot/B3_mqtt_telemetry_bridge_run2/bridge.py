# CHOSEN-TARGET: gmqtt
"""bridge -- publish sensor telemetry to an MQTT broker.



require glue -- hence Category B. The client is dependency-injected for testing.
"""
import asyncio

from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _GmqttClientAdapter:
    """Adapts gmqtt's asyncio-based client to the synchronous, paho-like
    interface used by TelemetryBridge (``connect`` and ``publish``)."""

    def __init__(self, cfg: BridgeConfig):
        import gmqtt

        self._gmqtt = gmqtt
        self._cfg = cfg
        self._client = gmqtt.Client(cfg.client_id)
        self._loop = self._get_loop()

    @staticmethod
    def _get_loop():
        try:
            return asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop

    def _run(self, coro):
        return self._loop.run_until_complete(coro)

    def connect(self, host, port):
        self._run(self._client.connect(host, port))

    def publish(self, topic, payload, qos=0):
        self._client.publish(topic, payload, qos=qos)

        class _PublishInfo:
            rc = 0

        return _PublishInfo()


def _default_client(cfg: BridgeConfig):
    return _GmqttClientAdapter(cfg)


class TelemetryBridge:
    def __init__(self, cfg: BridgeConfig, client=None):
        self.cfg = cfg
        self.client = client if client is not None else _default_client(cfg)

    def publish_reading(self, tag: str, reading: dict) -> dict:
        topic = topic_for(self.cfg.base_topic, tag)
        payload = encode(reading)
        info = self.client.publish(topic, payload, qos=self.cfg.qos)
        return {
            "topic": topic,
            "payload": payload,
            "qos": self.cfg.qos,
            "rc": getattr(info, "rc", 0),
        }

    def publish_batch(self, readings: dict) -> list:
        """Publish {tag: reading} pairs, returning the per-message results."""
        return [self.publish_reading(tag, r) for tag, r in readings.items()]
