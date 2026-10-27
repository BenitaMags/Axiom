"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


# ──────────────────────────────────────────────────────────────────────
# AXIOM CONNECTOR: paho-mqtt → amqtt
# The following functions bridge API incompatibilities between
# paho-mqtt and amqtt. They are injected by the AXIOM
# connector agent and called in place of the original API.
# ──────────────────────────────────────────────────────────────────────
import asyncio


class _axiom_sync_amqtt_client_wrapper:
    """Synchronous facade around amqtt.client.MQTTClient.

    paho-mqtt's `mqtt.Client(client_id=...)` returned an object whose
    `connect()`, `publish()`, and `disconnect()` methods were plain blocking
    calls -- they executed immediately and returned ordinary values (or
    raised) without any event loop involved.

    amqtt's `MQTTClient` exposes the equivalent operations as coroutines
    (`await client.connect(uri)`, `await client.publish(...)`,
    `await client.disconnect()`) that must be driven by an asyncio event
    loop.

    This wrapper owns a private event loop and uses
    `loop.run_until_complete()` to run each amqtt coroutine to completion
    synchronously, so callers get back plain values (or see exceptions
    raised directly) exactly as paho-mqtt's blocking API did. This lets
    `TelemetryBridge.publish_reading`/`publish_batch` remain ordinary
    synchronous methods returning dict/list values, matching the original
    paho-mqtt call semantics and the existing synchronous test
    expectations. Any exception raised inside an amqtt coroutine propagates
    out of `run_until_complete()` unchanged, so error behavior is preserved.
    """

    def __init__(self, client_id=None):
        from amqtt.client import MQTTClient

        self._loop = asyncio.new_event_loop()
        self._client = MQTTClient(client_id=client_id)

    def connect(self, uri):
        """Blocking equivalent of paho's `client.connect(host, port)`."""
        return self._loop.run_until_complete(self._client.connect(uri))

    def publish(self, topic, payload, qos=0):
        """Blocking equivalent of paho's `client.publish(topic, payload)`."""
        return self._loop.run_until_complete(
            self._client.publish(topic, payload, qos=qos)
        )

    def disconnect(self):
        """Blocking equivalent of paho's `client.disconnect()`."""
        return self._loop.run_until_complete(self._client.disconnect())


def _create_amqtt_client(cfg):
    """Factory replacing paho.mqtt.client.Client(client_id=cfg.client_id).

    Original: `mqtt.Client(client_id=cfg.client_id)` returned an unconnected,
    synchronous paho-mqtt client object; the caller then invoked the blocking
    `client.connect(cfg.host, cfg.port)` to establish the broker connection
    before using it for publishing.

    New: builds a `_axiom_sync_amqtt_client_wrapper` (itself wrapping an
    `amqtt.client.MQTTClient` with the same `client_id`) and synchronously
    connects it using a composed 'mqtt://host:port' URI (amqtt's connect API
    takes a single broker URI string rather than separate host/port
    arguments). The returned client is already connected, matching the
    fully-initialized state the original synchronous code produced after
    calling both `mqtt.Client(...)` and `.connect(...)`.

    Any exception raised while connecting (e.g. connection refused, DNS
    failure) propagates unchanged to the caller, mirroring the original
    paho `client.connect()` behavior of raising on connection errors rather
    than silently failing.
    """
    client = _axiom_sync_amqtt_client_wrapper(client_id=cfg.client_id)
    uri = f"mqtt://{cfg.host}:{cfg.port}"
    client.connect(uri)
    return client


def _default_client(cfg: BridgeConfig):
    """Default-factory used when no client is injected.

    Original synchronous code built a paho `mqtt.Client(...)` and then called
    its blocking `.connect(host, port)`. The amqtt-backed wrapper client is
    connected as part of construction via `_create_amqtt_client`, so this
    simply returns that already-connected client.
    """
    return _create_amqtt_client(cfg)


class TelemetryBridge:
    def __init__(self, cfg: BridgeConfig, client=None):
        self.cfg = cfg
        self.client = client

    def _ensure_client(self):
        """Lazily create/connect the client on first use."""
        if self.client is None:
            self.client = _default_client(self.cfg)
        return self.client

    def publish_reading(self, tag: str, reading: dict) -> dict:
        topic = topic_for(self.cfg.base_topic, tag)
        payload = encode(reading)
        client = self._ensure_client()
        # The wrapper's publish() returns None and raises on failure, unlike
        # paho-mqtt's publish() which returned a MQTTMessageInfo with an
        # `.rc` status code. Since no exception was raised here, the
        # operation succeeded, so we report rc=0 to match paho's success
        # code -- any failure instead propagates as a raised exception,
        # exactly as paho's connect()/publish() would raise on error.
        client.publish(topic, payload, qos=self.cfg.qos)
        return {
            "topic": topic,
            "payload": payload,
            "qos": self.cfg.qos,
            "rc": 0,
        }

    def publish_batch(self, readings: dict) -> list:
        """Publish {tag: reading} pairs, returning the per-message results."""
        return [self.publish_reading(tag, r) for tag, r in readings.items()]
