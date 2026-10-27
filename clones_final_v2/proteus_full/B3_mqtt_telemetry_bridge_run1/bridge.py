"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for

import asyncio
from amqtt.client import MQTTClient


class _AmqttSyncFacade:
    """Synchronous facade around amqtt.MQTTClient.

    paho-mqtt's ``Client.connect()``/``publish()`` are plain synchronous
    calls, and ``publish()`` returns an ``MQTTMessageInfo`` object exposing
    an ``rc`` return-code attribute (0 on success). amqtt's ``MQTTClient``
    instead exposes coroutines (``connect()``/``publish()``) that must be
    awaited on an asyncio event loop, and its ``publish()`` returns ``None``
    on success or raises an exception on failure -- there is no ``rc``
    attribute at all.

    This facade owns a private asyncio event loop and uses
    ``loop.run_until_complete()`` to drive the amqtt coroutines from
    synchronous call sites, and synthesizes a small result object with an
    ``rc`` attribute (0 for success, 1 if amqtt raised during publish) so
    existing ``getattr(info, "rc", 0)`` call sites keep working unchanged.
    This only masks the async/sync mismatch rather than resolving it, but it
    preserves the existing synchronous contract of TelemetryBridge.
    """

    class _Result:
        def __init__(self, rc):
            self.rc = rc

    def __init__(self, client_id, host, port):
        self._loop = asyncio.new_event_loop()
        self._client = MQTTClient(client_id=client_id, loop=self._loop)
        uri = f"mqtt://{host}:{port}/"
        self._loop.run_until_complete(self._client.connect(uri))

    def publish(self, topic, payload, qos=0):
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        try:
            self._loop.run_until_complete(
                self._client.publish(topic, payload, qos=qos)
            )
            return self._Result(0)
        except Exception:
            return self._Result(1)

    def disconnect(self):
        self._loop.run_until_complete(self._client.disconnect())
        self._loop.close()


def _axiom_amqtt_client_wrapper(client_id, host, port):
    """Build a synchronous facade over amqtt.MQTTClient for a given client id/host/port.

    paho-mqtt callers construct a client with ``mqtt.Client(client_id=...)``
    and then call synchronous ``connect()``/``publish()`` methods whose
    ``publish()`` result carries an ``rc`` attribute. amqtt has no
    synchronous, drop-in equivalent -- its client is coroutine-based. This
    wrapper returns a ``_AmqttSyncFacade`` that internally drives amqtt's
    async connect/publish via a private event loop and exposes the same
    ``.publish(...)`` -> object-with-``.rc`` shape that existing call sites
    expect.
    """
    return _AmqttSyncFacade(client_id, host, port)


def _default_client(cfg: BridgeConfig):

    return _axiom_amqtt_client_wrapper(cfg.client_id, cfg.host, cfg.port)


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
