"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.

Since aiomqtt is async-only, this module provides a thin synchronous adapter
(`_AiomqttSyncAdapter`) that drives an `aiomqtt.Client` from synchronous code using
an internal event loop, so the public (synchronous) API of `TelemetryBridge` is
preserved exactly.
"""
import asyncio

from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _AiomqttSyncAdapter:
    """Wraps an aiomqtt.Client to provide a synchronous publish() API
    compatible with the shape expected by TelemetryBridge (an object with a
    .publish(topic, payload, qos=...) method returning something with an
    'rc' attribute, mirroring paho-mqtt's MQTTMessageInfo)."""

    def __init__(self, cfg: BridgeConfig):
        import aiomqtt  # lazy import for testability

        self._aiomqtt = aiomqtt
        self.cfg = cfg
        self._loop = asyncio.new_event_loop()
        self._client = aiomqtt.Client(
            hostname=cfg.host,
            port=cfg.port,
            identifier=cfg.client_id,
        )
        self._loop.run_until_complete(self._client.__aenter__())

    def publish(self, topic: str, payload: bytes, qos: int = 0):
        rc = self._loop.run_until_complete(self._publish_async(topic, payload, qos))
        return _PublishResult(rc)

    async def _publish_async(self, topic: str, payload: bytes, qos: int) -> int:
        try:
            await self._client.publish(topic, payload=payload, qos=qos)
            return 0
        except Exception:
            return 1

    def __del__(self):
        try:
            if self._loop and not self._loop.is_closed():
                self._loop.run_until_complete(self._client.__aexit__(None, None, None))
                self._loop.close()
        except Exception:
            pass


class _PublishResult:
    def __init__(self, rc: int):
        self.rc = rc


def _default_client(cfg: BridgeConfig):
    return _AiomqttSyncAdapter(cfg)


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
