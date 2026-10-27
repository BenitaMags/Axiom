"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import json
import asyncio
import threading


class _AxiomMqttSyncFacade:
    """Synchronous facade over an aiomqtt.Client, bridging paho-mqtt's
    blocking connect/publish lifecycle to aiomqtt's async-only API.

    paho-mqtt's `Client.connect()` blocks until the TCP/MQTT handshake is
    done, and the client object then stays connected for the life of the
    process; `client.publish(...)` returns an `MQTTMessageInfo` whose `.rc`
    is a paho result code (0 == MQTT_ERR_SUCCESS) that the caller inspects
    synchronously.

    aiomqtt has no such object: connecting and publishing are coroutines
    that must run inside an `async with aiomqtt.Client(...)` context, and
    `Client.publish()` returns `None` once the message has been handed to
    the underlying transport (it does not itself confirm broker delivery
    for qos 0, and even for qos>=1 it does not return a status object).

    To preserve the "construct once, then call synchronous methods"
    lifecycle that the rest of this module expects, this facade starts a
    dedicated background event loop thread that lives for as long as the
    facade does, enters the aiomqtt client's async context on that loop
    once (mirroring paho's blocking `connect`), and then marshals each
    subsequent synchronous call onto that same loop via
    `asyncio.run_coroutine_threadsafe(...).result()`. Using one persistent
    loop (instead of `asyncio.run` per call) is essential: `asyncio.run`
    tears its loop down when the coroutine finishes, which would leave the
    aiomqtt connection bound to a dead loop for every call after the
    first.

    `.result()` re-raises any exception the coroutine raised, so errors
    (e.g. a bad topic) propagate to the caller exactly as they would from
    paho's synchronous calls -- they are never swallowed here.
    """

    def __init__(self, host: str = "localhost", port: int = 1883):
        import aiomqtt

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._client = aiomqtt.Client(hostname=host, port=port)
        # Mirrors paho's blocking connect(): block here until the async
        # context (and underlying network connection) is established.
        self._run(self._client.__aenter__())

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def publish(self, topic: str, payload: bytes):
        """Publish `payload` to `topic` and return a paho-like result code.

        aiomqtt's `publish()` returns `None` -- it confirms only that the
        message was handed off, not that the broker acknowledged it. We
        therefore return 0 (paho's MQTT_ERR_SUCCESS) to mean "successfully
        queued/sent", matching what callers here previously read from
        `.rc`. This is honest about what we can confirm: a real publish
        failure raises inside `_run` and is not caught here, so it still
        propagates to the caller instead of being converted into a status
        code.
        """
        self._run(self._client.publish(topic, payload))
        return 0

    def close(self):
        self._run(self._client.__aexit__(None, None, None))
        self._loop.call_soon_threadsafe(self._loop.stop)


def _axiom_mqtt_client_factory_wrapper(host: str = "localhost", port: int = 1883):
    """Build a synchronous stand-in for a connected paho-mqtt `Client`.

    paho-mqtt's `mqtt.Client()` + `client.connect(host, port)` yields a
    live, synchronous object whose `.publish(...)` can be called directly.
    aiomqtt has no equivalent synchronous constructor -- everything is a
    coroutine used inside `async with`. `_AxiomMqttSyncFacade` bridges that
    gap with a persistent background event loop so the returned object can
    be used exactly like the old synchronous client.
    """
    return _AxiomMqttSyncFacade(host, port)


def _default_client(host: str = "localhost", port: int = 1883):
    return _axiom_mqtt_client_factory_wrapper(host, port)


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
