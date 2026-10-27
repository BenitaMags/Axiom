"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import asyncio
import json


class _AxiomMqttClientResult:
    """Small stand-in for paho-mqtt's ``MQTTMessageInfo``.

    paho-mqtt's ``Client.publish()`` returns a ``MQTTMessageInfo`` object
    exposing an ``.rc`` return-code attribute (0 == success). amqtt's
    publish coroutine returns ``None`` (or raises on failure) and has no
    such attribute. This tiny object fabricates the ``.rc`` attribute so
    existing call sites written as ``getattr(info, "rc", 0)`` keep working.
    """

    def __init__(self, rc: int):
        self.rc = rc


class _AxiomMqttClientWrapper:
    """Synchronous facade over amqtt's async ``MQTTClient``.

    paho-mqtt is used here synchronously: ``connect()`` and ``publish()``
    are plain blocking calls, and ``publish()`` returns an object with an
    ``.rc`` attribute. amqtt's ``amqtt.client.MQTTClient`` is asyncio-native:
    ``connect()``/``publish()`` are coroutines that must be awaited on an
    event loop, and they return ``None`` (or raise) rather than an ``.rc``
    carrying result object.

    This wrapper owns a private asyncio event loop and drives the amqtt
    coroutines to completion on it via ``loop.run_until_complete()``,
    exposing blocking ``connect()``/``publish()`` methods that mirror
    paho's synchronous interface. ``publish()`` also wraps the outcome in
    an ``_AxiomMqttClientResult`` so callers can keep reading ``.rc`` the
    way they did against paho-mqtt.
    """

    def __init__(self, host: str = "localhost", port: int = 1883):
        from amqtt.client import MQTTClient

        self._loop = asyncio.new_event_loop()
        self._client = MQTTClient()
        self._uri = f"mqtt://{host}:{port}"

    def connect(self, host: str = None, port: int = None) -> int:
        if not (host is None):
            uri = f"mqtt://{host}:{port or 1883}"
        else:
            uri = self._uri
        self._loop.run_until_complete(self._client.connect(uri))
        return 0

    def publish(self, topic: str, payload: bytes) -> "_AxiomMqttClientResult":
        try:
            self._loop.run_until_complete(self._client.publish(topic, payload))
            _spt_r1 = _AxiomMqttClientResult(0)
            return _spt_r1
        except Exception:
            _spt_r2 = _AxiomMqttClientResult(1)
            return _spt_r2


def _axiom_mqtt_client_wrapper(host: str = "localhost", port: int = 1883):
    """Build and connect an ``_AxiomMqttClientWrapper`` bound to host/port.

    Replaces the paho-mqtt construction pattern ``mqtt.Client()`` followed
    by ``client.connect(host, port)`` with amqtt's async client wrapped for
    synchronous use, so callers keep the same blocking connect-then-publish
    flow they relied on with paho-mqtt.
    """
    client = _AxiomMqttClientWrapper(host, port)
    client.connect()
    return client


def _default_client(host: str = "localhost", port: int = 1883):
    _spt_r3 = _axiom_mqtt_client_wrapper(host, port)

    return _spt_r3


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        if not (client is not None):
            self.client = _default_client()
        else:
            self.client = client

    def publish(self, tag: str, record: dict) -> int:
        _spt_r4 = getattr(self.client.publish(f"{self.topic_base}/{tag}", json.dumps(record, separators=(",", ":")).encode("utf-8")), "rc", 0)
        return _spt_r4
