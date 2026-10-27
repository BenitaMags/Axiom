"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _PublishResult:
    """Mimics paho-mqtt's MQTTMessageInfo enough for `.rc` access.

    paho-mqtt's `Client.publish()` returns an MQTTMessageInfo object whose
    `.rc` attribute is 0 on success and a non-zero MQTT_ERR_* code on
    failure. amqtt's `MQTTClient.publish()` is a coroutine that returns
    None on success and raises on failure. This tiny holder just carries
    an `.rc` integer so callers (e.g. publish_reading) that read `.rc`
    keep working unchanged.
    """

    def __init__(self, rc: int):
        self.rc = rc


class _amqtt_sync_client_shim:
    """Synchronous facade over amqtt.client.MQTTClient.

    paho-mqtt's `Client` is synchronous: `.connect(host, port)` blocks
    until connected, and `.publish(topic, payload, qos=...)` blocks and
    returns an MQTTMessageInfo with `.rc` (0 on success, non-zero on
    failure). amqtt's `MQTTClient` is asyncio-based: `.connect(uri)` and
    `.publish(topic, payload, qos=...)` are coroutines that must be
    awaited, and `.publish()` raises on failure instead of returning a
    status code.

    This shim owns a private asyncio event loop and drives those
    coroutines with `run_until_complete`, translating amqtt's
    "return None or raise" publish behavior back into paho's
    "return an object with .rc" behavior so existing synchronous callers
    (like TelemetryBridge.publish_reading) don't need to change.
    """

    def __init__(self, client_id: str):
        import asyncio
        from amqtt.client import MQTTClient

        self._loop = asyncio.new_event_loop()
        self._client = MQTTClient(client_id=client_id)

    def connect(self, host, port):
        uri = f"mqtt://{host}:{port}"
        return self._loop.run_until_complete(self._client.connect(uri))

    def publish(self, topic, payload, qos=0):
        self._loop.run_until_complete(self._client.publish(topic, payload, qos=qos))
        return _PublishResult(0)


def _default_client(cfg: BridgeConfig):
    client = _amqtt_sync_client_shim(client_id=cfg.client_id)
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
