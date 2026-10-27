# CHOSEN-TARGET: gmqtt
"""bridge -- publish sensor telemetry to an MQTT broker.



require glue -- hence Category B. The client is dependency-injected for testing.
"""
import asyncio

from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _GmqttClientAdapter:
    """Thin synchronous adapter around gmqtt.Client to preserve the
    paho-mqtt-like `connect` / `publish` API used by TelemetryBridge.
    """

    def __init__(self, cfg: BridgeConfig):
        import gmqtt

        self._gmqtt = gmqtt
        self._cfg = cfg
        self._client = gmqtt.Client(cfg.client_id)
        self._loop = asyncio.new_event_loop()
        self._connected = False
        self.connect(cfg.host, cfg.port)

    def connect(self, host, port):
        async def _connect():
            await self._client.connect(host, port)

        self._loop.run_until_complete(_connect())
        self._connected = True

    def publish(self, topic, payload, qos=0):
        self._client.publish(topic, payload, qos=qos)

        class _Info:
            rc = 0

        return _Info()


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
