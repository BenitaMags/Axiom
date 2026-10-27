"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for

import asyncio


def _axiom_mqtt_client_wrapper(cfg: BridgeConfig):
    """Bridge for paho-mqtt's ``mqtt.Client(client_id=...)`` constructor.

    paho-mqtt returns a plain, unconnected client object immediately; the
    caller later connects it with a separate blocking ``.connect()`` call
    and then reuses the same object for many blocking ``.publish()`` calls.

    aiomqtt has no bare constructor that yields a ready-to-reuse handle:
    ``aiomqtt.Client(...)`` only actually connects when entered as an
    ``async with`` context manager, and a fresh connection normally lives
    only for the lifetime of that ``with`` block.

    This wrapper defers building the real ``aiomqtt.Client`` until
    ``_axiom_mqtt_connect_wrapper`` runs. It returns a small handle object
    that carries the config and a dedicated event loop, so call sites can
    keep treating "construct" and "connect" as two separate synchronous
    steps, just like with paho-mqtt.
    """
    import aiomqtt  # target package; referenced only inside this adapter

    class _Handle:
        def __init__(self):
            self.cfg = cfg
            self.loop = asyncio.new_event_loop()
            self.aiomqtt_client = None
            self._ctx = None
            self._aiomqtt = aiomqtt

    return _Handle()


def _axiom_mqtt_connect_wrapper(client, host, port):
    """Bridge for paho-mqtt's blocking ``Client.connect(host, port)``.

    paho-mqtt's ``connect`` performs a blocking handshake and leaves the
    client object connected and reusable for subsequent publish calls.

    aiomqtt only establishes a connection through
    ``async with aiomqtt.Client(...) as c:`` -- an async context manager
    whose ``__aenter__`` coroutine performs the connection, and which is
    normally paired with an ``__aexit__`` on exit.

    This wrapper builds the real ``aiomqtt.Client`` for the given
    host/port and drives its ``__aenter__`` to completion on the handle's
    private event loop, storing the connected client on the handle so it
    keeps behaving like the always-connected object paho-mqtt call sites
    expect.
    """
    client._ctx = client._aiomqtt.Client(
        hostname=host, port=port, identifier=client.cfg.client_id
    )
    client.aiomqtt_client = client.loop.run_until_complete(client._ctx.__aenter__())
    return client


def _axiom_mqtt_publish_wrapper(client, topic, payload, qos=0):
    """Bridge for paho-mqtt's blocking ``Client.publish(topic, payload, qos=...)``.

    paho-mqtt's ``publish`` returns an ``MQTTMessageInfo``-like object
    exposing an integer return code via ``.rc`` (0 means success), which
    calling code inspects synchronously right after the call.

    aiomqtt's ``publish`` is a coroutine (``await client.publish(...)``)
    that returns ``None`` on success -- there is no return code object,
    because failures are reported via raised exceptions instead.

    This wrapper drives that coroutine to completion on the handle's
    private event loop and fabricates a tiny result object exposing
    ``rc = 0``, so existing ``getattr(info, "rc", 0)`` call sites keep
    working unchanged.
    """
    client.loop.run_until_complete(
        client.aiomqtt_client.publish(topic, payload, qos=qos)
    )

    class _Result:
        rc = 0

    return _Result()


def _default_client(cfg: BridgeConfig):
    import paho.mqtt.client as mqtt  # lazy import for testability

    client = _axiom_mqtt_client_wrapper(cfg)
    _axiom_mqtt_connect_wrapper(client, cfg.host, cfg.port)
    return client


class TelemetryBridge:
    def __init__(self, cfg: BridgeConfig, client=None):
        self.cfg = cfg
        self.client = client if client is not None else _default_client(cfg)

    def publish_reading(self, tag: str, reading: dict) -> dict:
        topic = topic_for(self.cfg.base_topic, tag)
        payload = encode(reading)
        info = _axiom_mqtt_publish_wrapper(self.client, topic, payload, qos=self.cfg.qos)
        return {
            "topic": topic,
            "payload": payload,
            "qos": self.cfg.qos,
            "rc": getattr(info, "rc", 0),
        }

    def publish_batch(self, readings: dict) -> list:
        """Publish {tag: reading} pairs, returning the per-message results."""
        return [self.publish_reading(tag, r) for tag, r in readings.items()]
