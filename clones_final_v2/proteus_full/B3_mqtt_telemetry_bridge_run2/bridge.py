"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for

import asyncio


class _amqtt_sync_client_wrapper:
    """Synchronous-looking client wrapper around amqtt's asyncio MQTT client.

    paho-mqtt's ``mqtt.Client(client_id=...)`` produces an object with
    synchronous ``.connect(host, port)`` and ``.publish(topic, payload,
    qos=...)`` methods; ``.publish()`` returns an ``MQTTMessageInfo`` whose
    ``.rc`` attribute reports 0 on success.

    amqtt's client (``amqtt.client.MQTTClient``) is asyncio-native: its
    ``connect()`` takes a URI string and its ``connect()``/``publish()``
    methods are coroutines that must be awaited from within an event loop.
    ``publish()`` returns ``None`` (or raises) instead of an object with
    ``.rc``.

    This wrapper keeps a single private ``asyncio`` event loop alive for its
    whole lifetime and uses ``run_until_complete`` to drive the amqtt
    coroutines synchronously, exposing the same ``.connect(host, port)`` /
    ``.publish(topic, payload, qos=...)`` shape the rest of this module
    expects, and fabricating a small object with an ``.rc`` attribute (0 on
    success, 1 if the coroutine raised) so existing ``getattr(info, "rc", 0)``
    call sites keep working unmodified.
    """

    class _PublishInfo:
        def __init__(self, rc):
            self.rc = rc

    def __init__(self, client_id=None):
        from amqtt.client import MQTTClient

        self._loop = asyncio.new_event_loop()
        self._client = MQTTClient(client_id=client_id)

    def connect(self, host, port):
        uri = f"mqtt://{host}:{port}"
        self._loop.run_until_complete(self._client.connect(uri))

    def publish(self, topic, payload, qos=0):
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        try:
            self._loop.run_until_complete(
                self._client.publish(topic, payload, qos=qos)
            )
            rc = 0
        except Exception:
            rc = 1
        return self._PublishInfo(rc)

    def disconnect(self):
        try:
            self._loop.run_until_complete(self._client.disconnect())
        finally:
            self._loop.close()


def _default_client(cfg: BridgeConfig):
    client = _amqtt_sync_client_wrapper(client_id=cfg.client_id)
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
