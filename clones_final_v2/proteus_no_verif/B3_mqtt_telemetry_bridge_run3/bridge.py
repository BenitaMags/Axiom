"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for

import asyncio


class _AxiomPublishResult:
    """Small stand-in for paho-mqtt's MQTTMessageInfo.

    paho-mqtt's ``client.publish(...)`` returns an ``MQTTMessageInfo`` object
    exposing an ``.rc`` attribute (the result code of the publish call).
    amqtt's ``MQTTClient.publish(...)`` coroutine returns ``None`` (or raises
    on failure) instead. Callers in this module read ``getattr(info, "rc", 0)``,
    so we fabricate an object with an ``.rc`` attribute to preserve that
    calling convention without touching the call sites' expectations.
    """

    def __init__(self, rc: int = 0):
        self.rc = rc


class _AxiomMQTTClientShim:
    """Synchronous-looking wrapper around amqtt's async MQTTClient.

    paho-mqtt's ``Client`` is synchronous/callback-driven: you construct it,
    call the blocking ``.connect(host, port)``, and later call the blocking
    ``.publish(topic, payload, qos=...)`` which returns an object with an
    ``.rc`` result code. amqtt's ``MQTTClient`` is asyncio-based: connection
    is established via ``await client.connect(uri)`` and messages are sent
    via ``await client.publish(topic, payload, qos=...)`` which returns
    ``None``. This shim owns a private event loop and uses
    ``loop.run_until_complete(...)`` to drive those coroutines from ordinary
    synchronous method calls, and wraps the ``None`` result in an
    ``_AxiomPublishResult`` so existing ``.rc`` access keeps working.
    """

    def __init__(self, cfg: BridgeConfig):
        from amqtt.client import MQTTClient  # lazy import for testability

        self._loop = asyncio.new_event_loop()
        self._client = MQTTClient(client_id=cfg.client_id)
        uri = f"mqtt://{cfg.host}:{cfg.port}"
        self._loop.run_until_complete(self._client.connect(uri))

    def publish(self, topic, payload, qos: int = 0):
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        self._loop.run_until_complete(
            self._client.publish(topic, payload, qos=qos)
        )
        _spt_r1 = _AxiomPublishResult(0)
        return _spt_r1

    def disconnect(self):
        self._loop.run_until_complete(self._client.disconnect())


def _axiom_mqtt_client_wrapper(cfg: BridgeConfig):
    """Build and connect an amqtt-backed client usable like paho's Client.

    paho-mqtt call sites expect to do ``client = mqtt.Client(client_id=...)``
    followed by a blocking ``client.connect(host, port)``. amqtt requires an
    async ``MQTTClient(client_id=...)`` plus ``await client.connect(uri)``.
    This wrapper performs the equivalent construction+connect sequence
    synchronously (via ``_AxiomMQTTClientShim``) so ``_default_client`` can
    keep returning an already-connected, synchronously-usable client object.
    """
    _spt_r2 = _AxiomMQTTClientShim(cfg)
    return _spt_r2


def _default_client(cfg: BridgeConfig):
    _spt_r3 = _axiom_mqtt_client_wrapper(cfg)
    return _spt_r3


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
        _spt_r4 = {
            "topic": topic,
            "payload": payload,
            "qos": self.cfg.qos,
            "rc": getattr(self.client.publish(topic, payload, qos=self.cfg.qos), "rc", 0),
        }
        return _spt_r4

    def publish_batch(self, readings: dict) -> list:
        """Publish {tag: reading} pairs, returning the per-message results."""
        _spt_r5 = [self.publish_reading(tag, r) for tag, r in readings.items()]
        return _spt_r5
