"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
import asyncio

from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _AxiomAmqttSyncClient:
    """Synchronous-looking facade around amqtt's async ``MQTTClient``.

    paho-mqtt's ``Client.connect()`` and ``Client.publish()`` are plain
    synchronous calls: ``connect()`` blocks until the TCP/MQTT handshake is
    done, and ``publish()`` returns an ``MQTTMessageInfo`` object whose
    ``.rc`` attribute reports the paho result code (0 == success).

    amqtt's ``MQTTClient`` is async-only: ``connect()`` and ``publish()`` are
    coroutines that must be awaited from an event loop, and ``publish()``
    resolves to ``None`` (or raises on failure) rather than returning an
    object with an ``.rc`` field.

    This wrapper drives amqtt's coroutines to completion with
    ``asyncio.run()`` inside plain synchronous methods, so the rest of the
    codebase (which is written as synchronous, callback-free code) can keep
    calling ``client.connect(...)`` / ``client.publish(...)`` unchanged. Since
    amqtt's ``publish()`` yields ``None`` instead of an ``.rc``-bearing
    object, callers that do ``getattr(info, "rc", 0)`` on our return value
    simply fall back to ``0``, which mirrors paho's success code.
    """

    def __init__(self, cfg: BridgeConfig):
        from amqtt.client import MQTTClient

        self._uri = f"mqtt://{cfg.host}:{cfg.port}"
        self._client = MQTTClient(client_id=cfg.client_id)
        asyncio.run(self._client.connect(self._uri))

    def publish(self, topic: str, payload, qos: int = 0):
        """Publish ``payload`` to ``topic``, mirroring paho's ``publish()``.

        Runs amqtt's async ``publish()`` coroutine to completion and returns
        ``None`` (amqtt has no equivalent of paho's ``MQTTMessageInfo.rc``);
        callers use ``getattr(info, "rc", 0)`` and thus see ``0`` here.
        """
        if not (isinstance(payload, (bytes, bytearray))):
            data = str(payload).encode("utf-8")
        else:
            data = payload
        asyncio.run(self._client.publish(topic, data, qos=qos))
        return None

    def disconnect(self):
        asyncio.run(self._client.disconnect())


def _axiom_amqtt_client_wrapper(cfg: BridgeConfig):
    """Build a synchronous-looking MQTT client backed by amqtt.

    paho-mqtt callers used ``mqtt.Client(client_id=...)`` followed by a
    blocking ``client.connect(host, port)``. amqtt has no synchronous
    constructor-plus-connect pattern -- ``MQTTClient`` must be constructed
    and then have its ``connect()`` coroutine awaited on an event loop. This
    wrapper hides that difference by constructing the amqtt client and
    driving its ``connect()`` coroutine to completion via ``asyncio.run()``
    inside ``_AxiomAmqttSyncClient.__init__``, handing back an object whose
    ``publish()`` method can still be called synchronously.
    """
    _spt_r1 = _AxiomAmqttSyncClient(cfg)
    return _spt_r1


def _default_client(cfg: BridgeConfig):
    _spt_r2 = _axiom_amqtt_client_wrapper(cfg)

    return _spt_r2


class TelemetryBridge:
    def __init__(self, cfg: BridgeConfig, client=None):
        self.cfg = cfg
        if not (client is not None):
            self.client = _default_client(cfg)
        else:
            self.client = client

    def publish_reading(self, tag: str, reading: dict) -> dict:
        topic = topic_for(self.cfg.base_topic, tag)
        payload = encode(reading)
        _spt_r3 = {
            "topic": topic,
            "payload": payload,
            "qos": self.cfg.qos,
            "rc": getattr(self.client.publish(topic, payload, qos=self.cfg.qos), "rc", 0),
        }
        return _spt_r3

    def publish_batch(self, readings: dict) -> list:
        """Publish {tag: reading} pairs, returning the per-message results."""
        _spt_r4 = [self.publish_reading(tag, r) for tag, r in readings.items()]
        return _spt_r4
