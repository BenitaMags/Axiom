# CHOSEN-TARGET: gmqtt
"""bridge -- publish sensor telemetry to an MQTT broker.



require glue -- hence Category B. The client is dependency-injected for testing.
"""
import asyncio

from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _PublishInfo:
    """Adapter object mimicking paho's MQTTMessageInfo (has an `rc` attribute)."""

    __slots__ = ("rc",)

    def __init__(self, rc: int = 0):
        self.rc = rc


class _GmqttClientAdapter:
    """Thin adapter around gmqtt.Client exposing a paho-like sync API.

    gmqtt is asyncio-based, so this adapter manages its own event loop
    to provide the same synchronous `connect`/`publish` interface that
    the rest of the codebase (and its tests) expect from paho-mqtt.
    """

    def __init__(self, cfg: BridgeConfig):
        import gmqtt

        self._gmqtt = gmqtt
        self._cfg = cfg
        self._client = gmqtt.Client(cfg.client_id)
        self._loop = asyncio.new_event_loop()

    def connect(self, host: str, port: int):
        async def _do_connect():
            await self._client.connect(host, port)

        return self._loop.run_until_complete(_do_connect())

    def publish(self, topic: str, payload: bytes, qos: int = 0):
        self._client.publish(topic, payload, qos=qos)
        return _PublishInfo(rc=0)

    def __del__(self):
        try:
            if self._loop is not None and not self._loop.is_closed():
                self._loop.close()
        except Exception:
            pass


def _default_client(cfg: BridgeConfig):
    client = _GmqttClientAdapter(cfg)  # lazy import for testability (inside adapter)
    client.connect(cfg.host, cfg.port)
    return client


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
