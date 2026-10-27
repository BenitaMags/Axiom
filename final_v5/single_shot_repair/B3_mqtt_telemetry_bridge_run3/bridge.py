# CHOSEN-TARGET: amqtt
"""bridge -- publish sensor telemetry to an MQTT broker.



require glue -- hence Category B. The client is dependency-injected for testing.
"""
import asyncio

from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _PublishResult:
    """Mimics the return value of paho's client.publish(), which exposes
    an `.rc` attribute for the result code."""

    def __init__(self, rc: int = 0):
        self.rc = rc


class _AmqttClientAdapter:
    """Adapter that exposes a paho-like `.publish(topic, payload, qos=...)`
    synchronous method backed by amqtt's asyncio MQTTClient.

    amqtt's API is asynchronous (`await client.connect(uri)`,
    `await client.publish(topic, payload, qos)`), so this adapter owns an
    event loop and drives the coroutines synchronously to preserve the
    original synchronous, paho-like public surface used by TelemetryBridge.
    """

    def __init__(self, cfg: BridgeConfig):
        from amqtt.client import MQTTClient, QOS_0, QOS_1, QOS_2

        self._qos_map = {0: QOS_0, 1: QOS_1, 2: QOS_2}
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._client = MQTTClient(client_id=cfg.client_id)
        uri = f"mqtt://{cfg.host}:{cfg.port}/"
        self._loop.run_until_complete(self._client.connect(uri))

    def publish(self, topic: str, payload: bytes, qos: int = 0):
        amqtt_qos = self._qos_map.get(qos, self._qos_map[0])
        self._loop.run_until_complete(
            self._client.publish(topic, payload, qos=amqtt_qos)
        )
        return _PublishResult(rc=0)

    def disconnect(self):
        self._loop.run_until_complete(self._client.disconnect())
        self._loop.close()


def _default_client(cfg: BridgeConfig):
    return _AmqttClientAdapter(cfg)


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
