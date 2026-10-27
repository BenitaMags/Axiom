"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `amqtt` (async, asyncio-native). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
import asyncio

from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


def _axiom_mqtt_client_factory_wrapper(cfg: BridgeConfig):
    """Construct an amqtt-backed client that mimics paho-mqtt's sync API.

    paho-mqtt's `Client.connect(host, port)` connects synchronously, and its
    `Client.publish(topic, payload, qos)` returns a `MQTTMessageInfo`-like
    object exposing a numeric `.rc` result code that callers (see
    `TelemetryBridge.publish_reading`) inspect via `getattr(info, "rc", 0)`.

    amqtt's `MQTTClient` is asyncio-native instead: `connect(uri)` takes a
    single `mqtt://host:port` URI and must be awaited, and `publish(...)` is
    itself a coroutine with no `.rc`-bearing return value. Since the rest of
    this module (`TelemetryBridge`, `publish_reading`, `publish_batch`) is
    written and dependency-injected as plain synchronous code, this wrapper
    spins up a private asyncio event loop per call (via `asyncio.run`) to
    drive the amqtt coroutines to completion, and synthesizes a small object
    with an `.rc` attribute (0 on success, 1 on failure) so the existing
    synchronous call sites keep working without themselves becoming
    'async def'/'await' code.
    """
    import _Info  # connector: bound to wrapper scope
    from amqtt.client import MQTTClient

    class _SyncAmqttClient:
        def __init__(self, client_id):
            self._client = MQTTClient(client_id=client_id)

        def connect(self, host, port):
            uri = f"mqtt://{host}:{port}"
            asyncio.run(self._client.connect(uri))

        def publish(self, topic, payload, qos=0):
            class _Info:
                rc = 0

            async def _do_publish():
                await self._client.publish(topic, payload, qos=qos)

            try:
                asyncio.run(_do_publish())
                return _Info()
            except Exception:
                _Info.rc = 1
                return _Info()

    return _SyncAmqttClient(cfg.client_id)


def _default_client(cfg: BridgeConfig):
    client = _axiom_mqtt_client_factory_wrapper(cfg)
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
