"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
import asyncio
import threading

import aiomqtt

from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _AxiomMqttSyncFacade:
    """Synchronous facade bridging aiomqtt's async client onto the paho-mqtt-style
    synchronous interface (`.connect()` then `.publish() -> obj.rc`) that this
    module's call sites expect.

    paho-mqtt's `Client.connect()` blocks until the handshake completes, and
    `Client.publish()` returns a `MQTTMessageInfo` object whose `.rc` attribute
    is a paho result code (0 == MQTT_ERR_SUCCESS), retrieved here via
    `getattr(info, "rc", 0)`. aiomqtt has no equivalent: you connect by entering
    `aiomqtt.Client` as an async context manager, and `publish()` is a coroutine
    that either completes (meaning the message was queued/sent -- not necessarily
    acked) or raises `aiomqtt.MqttError` on failure; it never returns a status
    code. To keep the synchronous call/return-code contract this module relies
    on, this facade starts one persistent background event loop (so the
    connection survives across calls, unlike a fresh `asyncio.run` per call,
    which would tear down the connection's transport each time), drives the
    aiomqtt client's async lifecycle on that loop, and synthesizes `rc=0` only
    when `publish()` returns without raising -- which is the only success signal
    aiomqtt provides. Any exception aiomqtt raises is propagated unchanged to
    the caller (never caught/swallowed), exactly as paho-mqtt's own exceptions
    (e.g. `ValueError` on a wildcard topic) would reach the caller.
    """

    class _Result:
        def __init__(self, rc):
            self.rc = rc

    def __init__(self, cfg: BridgeConfig):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._client = aiomqtt.Client(cfg.host, port=cfg.port, identifier=cfg.client_id)
        self._run(self._client.__aenter__())

    def _run(self, coro):
        """Schedule `coro` on the persistent background loop, block until it
        finishes, and re-raise whatever exception it raised so failures reach
        the caller instead of being lost across the thread boundary."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def connect(self, host, port):
        """No-op: the aiomqtt connection is already established in __init__
        via `__aenter__` on the persistent loop, mirroring how the paho-mqtt
        client was already connect()-ed before being handed back to callers."""
        return None

    def publish(self, topic, payload, qos=0):
        self._run(self._client.publish(topic, payload, qos=qos))
        return _AxiomMqttSyncFacade._Result(0)

    def close(self):
        self._run(self._client.__aexit__(None, None, None))
        self._loop.call_soon_threadsafe(self._loop.stop)


def _axiom_mqtt_client_factory_wrapper(cfg: BridgeConfig):
    """Replaces `paho.mqtt.client.Client(client_id=...)` construction. Builds
    an aiomqtt-backed `_AxiomMqttSyncFacade`, which exposes the same
    `.connect()` / `.publish() -> obj.rc` surface the rest of this module
    (publish_reading, publish_batch) was written against, so those call sites
    do not need to become async themselves.
    """
    return _AxiomMqttSyncFacade(cfg)


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
