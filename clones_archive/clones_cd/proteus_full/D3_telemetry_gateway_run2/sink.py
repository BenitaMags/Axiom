"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import asyncio
import json
import threading


class _AxiomAsyncMqttBridge:
    """Runs a private asyncio event loop on a background thread.

    paho-mqtt's `Client` is a plain synchronous object: `.connect(host, port)`
    blocks until the socket is up, and `.publish(topic, payload)` blocks and
    returns a `MQTTMessageInfo` with an `.rc` result code, all callable from
    ordinary synchronous code with no event loop involved.

    aiomqtt has no equivalent synchronous, persistent client: `aiomqtt.Client`
    only manages a live connection for the duration of
    `async with aiomqtt.Client(host, port) as client: ...`, and both connecting
    and publishing are `await`-able coroutines that require a running event
    loop.

    This bridge keeps a real aiomqtt.Client connection open for the lifetime
    of the sink by driving it from a dedicated background thread that owns its
    own event loop. `connect()`/`publish()` calls made from synchronous code
    are marshalled onto that loop with `run_coroutine_threadsafe` and blocked
    on until the real (not faked) aiomqtt coroutine completes, so callers get
    back a client object usable the way `paho-mqtt` code expects.
    """

    def __init__(self, host: str, port: int):
        self._host = host
        self._port = port
        self._loop = asyncio.new_event_loop()
        self._loop_ready = threading.Event()
        self._client = None
        self._client_cm = None
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._loop_ready.wait()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop_ready.set()
        self._loop.run_forever()

    def _run_coroutine(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    def connect(self):
        import aiomqtt  # target package; used only inside the adapter

        async def _connect():
            self._client_cm = aiomqtt.Client(self._host, self._port)
            self._client = await self._client_cm.__aenter__()

        self._run_coroutine(_connect())
        return self

    def publish(self, topic: str, payload: bytes):
        async def _publish():
            await self._client.publish(topic, payload)

        self._run_coroutine(_publish())
        return 0


def _axiom_mqtt_client_wrapper(host: str = "localhost", port: int = 1883):
    """Bridge for `mqtt.Client()`.

    paho-mqtt: `mqtt.Client()` returns a plain synchronous object that can be
    reused across the process lifetime -- `.connect()` and `.publish()` are
    ordinary blocking method calls on it.

    aiomqtt: there is no synchronous, persistent client object -- `aiomqtt.Client`
    only exists as an async context manager tied to a running event loop for
    the duration of the `async with` block.

    This wrapper returns an `_AxiomAsyncMqttBridge`, which hosts a real
    aiomqtt.Client on a dedicated background event loop thread, so the rest of
    this module can keep treating the returned object as a persistent,
    synchronously-callable client, matching the shape paho-mqtt callers
    expect.
    """
    return _AxiomAsyncMqttBridge(host, port)


def _axiom_mqtt_connect_wrapper(client: "_AxiomAsyncMqttBridge", host: str, port: int):
    """Bridge for `client.connect(host, port)`.

    paho-mqtt: `client.connect(host, port)` synchronously opens the socket and
    returns `None`; the client is then connected for subsequent calls.

    aiomqtt: connecting only happens by entering
    `async with aiomqtt.Client(host, port) as client:`, which requires an
    active event loop and yields the connected client as the context value.

    This wrapper delegates to the bridge's `connect()`, which enters that
    async context manager on its background event loop thread and keeps it
    open, giving the same "now connected" effect paho-mqtt's `.connect()` call
    site expects without faking the network handshake.
    """
    return client.connect()


def _axiom_mqtt_publish_wrapper(client: "_AxiomAsyncMqttBridge", topic: str, payload: bytes):
    """Bridge for `client.publish(topic, payload)`.

    paho-mqtt: `client.publish(topic, payload)` blocks and returns a
    `MQTTMessageInfo` object whose `.rc` attribute is the result code (0 on
    success).

    aiomqtt: `client.publish(topic, payload)` is a coroutine that must be
    awaited inside the client's connected async context and resolves to
    `None` on success, raising an exception on failure instead of returning a
    result code.

    This wrapper schedules that coroutine onto the bridge's background event
    loop (where the aiomqtt client is actually connected) and blocks until it
    completes, returning `0` to stand in for the paho-mqtt `.rc` success code
    since aiomqtt signals failure via exceptions rather than a return value.
    """
    return client.publish(topic, payload)


def _default_client(host: str = "localhost", port: int = 1883):
    import paho.mqtt.client as mqtt  # lazy import for testability

    client = _axiom_mqtt_client_wrapper(host, port)
    _axiom_mqtt_connect_wrapper(client, host, port)
    return client


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = _axiom_mqtt_publish_wrapper(self.client, f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0) if not isinstance(info, int) else info
