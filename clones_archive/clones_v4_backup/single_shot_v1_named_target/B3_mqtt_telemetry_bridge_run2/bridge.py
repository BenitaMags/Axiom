"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.

Since `aiomqtt` is async-only, this module provides a small synchronous adapter
(`_AiomqttSyncClient`) that wraps an `aiomqtt.Client` and drives its async methods
via `asyncio.run`/an internal event loop, exposing a `publish(topic, payload, qos=...)`
method compatible with the previous paho-mqtt-based call sites.
"""
import asyncio

from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _PublishResult:
    """Mimics paho-mqtt's MQTTMessageInfo enough for our purposes (has `.rc`)."""

    def __init__(self, rc: int = 0):
        self.rc = rc


class _AiomqttSyncClient:
    """Synchronous adapter around an `aiomqtt.Client`.

    aiomqtt's Client is an async context manager; to preserve the previous
    synchronous, dependency-injectable API used by TelemetryBridge, we manage
    our own event loop and connection lifecycle here.
    """

    def __init__(self, cfg: BridgeConfig):
        import aiomqtt

        self._aiomqtt = aiomqtt
        self.cfg = cfg
        self._loop = asyncio.new_event_loop()
        self._client = aiomqtt.Client(
            hostname=cfg.host,
            port=cfg.port,
            identifier=cfg.client_id,
        )
        self._connected = False
        self._connect()

    def _connect(self):
        self._loop.run_until_complete(self._client.__aenter__())
        self._connected = True

    def publish(self, topic: str, payload: bytes, qos: int = 0):
        try:
            self._loop.run_until_complete(
                self._client.publish(topic, payload=payload, qos=qos)
            )
            return _PublishResult(rc=0)
        except Exception:
            return _PublishResult(rc=1)

    def __del__(self):
        if self._connected:
            try:
                self._loop.run_until_complete(self._client.__aexit__(None, None, None))
            except Exception:
                pass
        try:
            self._loop.close()
        except Exception:
            pass


def _default_client(cfg: BridgeConfig):
    return _AiomqttSyncClient(cfg)


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
