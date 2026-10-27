"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import json
import asyncio
import threading


class _AxiomAmqttClientWrapper:
    """Adapter that presents a paho-mqtt-like synchronous client interface
    backed by amqtt's async MQTTClient.

    paho-mqtt's `Client.connect()`/`publish()` are synchronous calls, and
    `publish()` returns an `MQTTMessageInfo` object exposing an `.rc` return
    code attribute. amqtt's `MQTTClient` is coroutine-based: `connect()` and
    `publish()` are `async def` methods that must run inside an asyncio
    event loop, and `publish()` returns `None` (or raises on failure) rather
    than a status object.

    To bridge this without changing MqttSink's synchronous public API, this
    wrapper starts a single dedicated background thread running one
    persistent asyncio event loop for the lifetime of the wrapper. amqtt's
    internal primitives (locks, queues, transports) are bound to the loop
    active when `connect()` was awaited, so reusing `asyncio.run()` per call
    would spin up a fresh loop each time and break the live connection;
    instead every coroutine is scheduled on that same background loop via
    `asyncio.run_coroutine_threadsafe`, and the calling thread blocks on the
    resulting future to keep the call synchronous. `publish()` returns a
    simple object with an `.rc` attribute (0 on success, 1 on failure) so
    the shape matches what the paho-based caller expects.
    """

    class _Info:
        def __init__(self, rc):
            self.rc = rc

    def __init__(self, host="localhost", port=1883):
        from amqtt.client import MQTTClient  # lazy import for testability

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._client = MQTTClient()

        future = asyncio.run_coroutine_threadsafe(
            self._client.connect(f"mqtt://{host}:{port}"), self._loop
        )
        future.result()

    def publish(self, topic: str, payload: bytes):
        future = asyncio.run_coroutine_threadsafe(
            self._client.publish(topic, payload), self._loop
        )
        try:
            future.result()
            return self._Info(0)
        except Exception:
            return self._Info(1)


def _axiom_mqtt_client_wrapper(host: str = "localhost", port: int = 1883):
    """Factory returning a synchronous-looking MQTT client backed by amqtt.

    Historically `_default_client()` returned a paho-mqtt `Client` that was
    already connected via a synchronous `connect()` call, exposing a
    synchronous `publish()` used directly by `MqttSink.publish()`. amqtt has
    no equivalent synchronous, already-connected client: connecting and
    publishing are both coroutines that must run on an asyncio event loop.
    This function hides that mismatch by constructing an
    `_AxiomAmqttClientWrapper`, which owns a dedicated background event loop
    thread and exposes the same synchronous `publish()` call shape (an
    object with an `.rc` attribute) that the rest of this module expects.
    """
    return _AxiomAmqttClientWrapper(host, port)


def _default_client(host: str = "localhost", port: int = 1883):
    return _axiom_mqtt_client_wrapper(host, port)


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
