"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import asyncio
import json
import threading


class _AxiomMqttClientAdapter:
    """Synchronous-looking facade around amqtt's asyncio-only MQTTClient.

    paho-mqtt's mqtt.Client() is a plain object whose .connect() and
    .publish() block the calling thread and return immediately (connect)
    or an rc-bearing MQTTMessageInfo (publish). amqtt's MQTTClient only
    exposes coroutines (`await client.connect(uri)`, `await
    client.publish(...)`), so every call must be scheduled on an asyncio
    event loop.

    To keep the connection alive across calls (instead of opening/closing
    a new loop -- and therefore a new transport -- on every single call,
    which is what `asyncio.run()` per call would silently do), this
    adapter starts one background event loop in a daemon thread that lives
    for the lifetime of the adapter, and runs each amqtt coroutine on that
    same loop via `asyncio.run_coroutine_threadsafe(...).result()`. That
    call blocks the caller's thread until the coroutine finishes and
    re-raises whatever exception it raised, so failures are not swallowed.
    """

    def __init__(self):
        from amqtt.client import MQTTClient

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._client = MQTTClient()

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()


def _axiom_mqtt_client_factory() -> _AxiomMqttClientAdapter:
    """Build the client object used by `_default_client`.

    paho-mqtt: `mqtt.Client()` returns a ready-to-use, synchronous client
    object immediately. amqtt's equivalent, `amqtt.client.MQTTClient()`,
    is only usable from inside a running asyncio event loop. This factory
    returns `_AxiomMqttClientAdapter`, which owns a persistent background
    loop and the underlying `MQTTClient`, so the rest of the code can keep
    treating "the client" as a single synchronous object.
    """
    return _AxiomMqttClientAdapter()


def _axiom_mqtt_connect_wrapper(client, host: str, port: int) -> None:
    """Connect `client` to the broker.

    paho-mqtt: `client.connect(host, port)` is synchronous and blocks
    until the TCP/MQTT handshake completes (or raises). amqtt only offers
    `await client.connect(uri)`, where `uri` is a full `mqtt://host:port`
    connection string. This wrapper builds that URI and runs the coroutine
    to completion on the adapter's persistent background loop, via
    `_run`, so the call blocks the caller exactly like paho's did and any
    connection error raised by amqtt propagates to the caller unchanged.

    Tests (and other callers) may inject a client double that already
    exposes a plain, synchronous `.connect(host, port)` method instead of
    our `_AxiomMqttClientAdapter` -- such doubles have no `_run`/`_client`
    attributes, so this wrapper dispatches directly to `.connect(host,
    port)` in that case rather than assuming the amqtt-backed shape.
    """
    if hasattr(client, "_run") and hasattr(client, "_client"):
        uri = f"mqtt://{host}:{port}"
        client._run(client._client.connect(uri))
    else:
        client.connect(host, port)


def _axiom_mqtt_publish_wrapper(client, topic: str, payload: bytes) -> int:
    """Publish `payload` to `topic` and return a paho-style rc code.

    paho-mqtt: `client.publish(topic, payload)` returns a
    `MQTTMessageInfo` whose `.rc` attribute is `0` (`MQTT_ERR_SUCCESS`) if
    the message was successfully queued, and only *raises* for programmer
    errors such as a malformed topic. amqtt's `await client.publish(...)`
    returns `None` and gives no delivery acknowledgement at the default
    QoS -- it only confirms the publish was *queued*, not delivered, and
    it raises on failure instead of returning a status.

    This wrapper therefore runs the publish coroutine on the persistent
    background loop and, if it completes without raising, returns `0` to
    mean "queued successfully" -- mirroring paho's `MQTT_ERR_SUCCESS`
    contract for the success case. It does NOT catch and convert
    exceptions into a failure rc: amqtt gives no such status-for-failure
    contract to reproduce, and paho itself let real errors (e.g. bad
    topics) raise rather than returning a nonzero rc for them. Any
    exception amqtt raises here is therefore left to propagate to the
    caller, exactly as paho's would have.

    Tests (and other callers) may inject a client double that already
    exposes a plain, synchronous `.publish(topic, payload)` method instead
    of our `_AxiomMqttClientAdapter` -- such doubles have no
    `_run`/`_client` attributes, so this wrapper dispatches directly to
    `.publish(topic, payload)` in that case and returns whatever int rc
    it reports (defaulting to `0` if it returns nothing), rather than
    assuming the amqtt-backed shape.
    """
    if hasattr(client, "_run") and hasattr(client, "_client"):
        client._run(client._client.publish(topic, payload))
        return 0
    rc = client.publish(topic, payload)
    return 0 if rc is None else rc


def _default_client(host: str = "localhost", port: int = 1883):
    client = _axiom_mqtt_client_factory()
    _axiom_mqtt_connect_wrapper(client, host, port)
    return client


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        return _axiom_mqtt_publish_wrapper(self.client, f"{self.topic_base}/{tag}", payload)
