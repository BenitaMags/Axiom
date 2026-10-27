# CHOSEN-TARGET: gmqtt
"""bridge -- publish sensor telemetry to an MQTT broker.



require glue -- hence Category B. The client is dependency-injected for testing.
"""
import asyncio

from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _PublishResult:
    """Small shim mimicking paho's MQTTMessageInfo(.rc) shape."""

    __slots__ = ("rc",)

    def __init__(self, rc: int = 0):
        self.rc = rc


class _GmqttClientAdapter:
    """Adapts gmqtt.Client (asyncio-based) to the small synchronous surface
    this module needs: `.publish(topic, payload, qos=...)` returning an
    object with `.rc`.

    gmqtt has no blocking API of its own, so this adapter owns a private
    event loop, keeps it alive for the lifetime of the client (rather than
    creating a fresh loop per call), and drives connect/publish to
    completion synchronously via `run_until_complete`.
    """

    def __init__(self, cfg: BridgeConfig):
        import gmqtt  # lazy import for testability

        self._gmqtt = gmqtt
        self.cfg = cfg

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        self._client = gmqtt.Client(cfg.client_id)
        self._loop.run_until_complete(self._connect())

    async def _connect(self):
        await self._client.connect(self.cfg.host, self.cfg.port)

    def publish(self, topic: str, payload: bytes, qos: int = 0):
        try:
            self._client.publish(topic, payload, qos=qos)
            # Give the loop a turn so gmqtt actually writes the queued
            # packet to the transport before we return control to the
            # (synchronous) caller.
            self._loop.run_until_complete(asyncio.sleep(0))
            rc = 0
        except Exception:
            rc = 1
        return _PublishResult(rc)

    def __del__(self):
        try:
            if self._loop and not self._loop.is_closed():
                self._loop.run_until_complete(self._client.disconnect())
                self._loop.close()
        except Exception:
            pass


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
