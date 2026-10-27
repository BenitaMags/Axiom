"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
import asyncio

from amqtt.client import MQTTClient

from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _AmqttClientWrapper:
    """Synchronous, paho-mqtt-like facade over amqtt's asyncio MQTTClient.

    paho-mqtt's ``Client.connect()``/``Client.publish()`` are plain
    synchronous calls, and ``publish()`` returns a ``MQTTMessageInfo`` object
    exposing an ``rc`` result code. amqtt's ``MQTTClient`` is asyncio-based:
    ``connect()``/``publish()``/``disconnect()`` are coroutines, ``connect()``
    takes a broker URI instead of separate host/port arguments, and
    ``publish()`` returns ``None`` with no result code at all.

    This wrapper drives the underlying coroutines to completion with
    ``asyncio.run()`` so callers can keep issuing plain synchronous calls,
    builds the ``mqtt://host:port/`` URI amqtt expects, and synthesizes a
    tiny result object with an ``rc`` attribute (``0`` on success, since
    ``asyncio.run`` raises on failure rather than returning an error code)
    so existing code that does ``getattr(info, "rc", 0)`` keeps working
    unchanged.
    """

    class _PublishResult:
        def __init__(self, rc: int = 0):
            self.rc = rc

    def __init__(self, client_id: str):
        self._client = MQTTClient(client_id=client_id)

    def connect(self, host: str, port: int):
        uri = f"mqtt://{host}:{port}/"
        asyncio.run(self._client.connect(uri))

    def publish(self, topic: str, payload, qos: int = 0):
        asyncio.run(self._client.publish(topic, payload, qos=qos))
        return self._PublishResult(rc=0)

    def disconnect(self):
        asyncio.run(self._client.disconnect())


def _amqtt_client_wrapper(client_id: str) -> _AmqttClientWrapper:
    """Factory building a sync-facing amqtt client for the given client_id.

    See ``_AmqttClientWrapper`` for the paho-mqtt/amqtt API differences this
    bridges (async coroutines vs. sync calls, connect URI shape, and the
    missing ``rc`` result code on publish).
    """
    return _AmqttClientWrapper(client_id)


def _default_client(cfg: BridgeConfig):
    client = _amqtt_client_wrapper(cfg.client_id)
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
