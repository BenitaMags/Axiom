"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import asyncio
import json
import threading


class _aiomqtt_sync_client_bridge:
    """Synchronous bridge that keeps an aiomqtt.Client alive on a background loop.

    paho-mqtt's mqtt.Client() is fully synchronous: Client() constructs an
    object, .connect(host, port) blocks until connected, and the same
    object is then reused for many .publish() calls. aiomqtt.Client, by
    contrast, is an async context manager: connecting only happens via
    'async with aiomqtt.Client(host, port) as client', and every operation
    (including publish) is a coroutine that must run inside a live event
    loop.

    To offer the same synchronous connect()-then-reuse pattern, this
    bridge starts a private background thread running a dedicated asyncio
    event loop. Its .connect() method schedules a coroutine on that loop
    which enters the aiomqtt.Client async context and keeps it open
    (stored as self.async_client) for as long as the bridge exists. Other
    wrappers (e.g. for publishing) submit coroutines to this same loop via
    asyncio.run_coroutine_threadsafe(...).result() to reuse that open
    connection synchronously.
    """

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self.async_client = None
        self._cm = None

    def connect(self, host: str, port: int) -> None:
        """Enter and hold an aiomqtt.Client(host, port) async context.

        Mimics paho's blocking client.connect(host, port) by running the
        __aenter__ of aiomqtt's async context manager on the background
        loop and waiting for it to finish before returning.
        """
        import aiomqtt

        async def _connect():
            self._cm = aiomqtt.Client(host, port)
            self.async_client = await self._cm.__aenter__()

        asyncio.run_coroutine_threadsafe(_connect(), self._loop).result()

    def publish(self, topic: str, payload: bytes) -> int:
        """Synchronous publish that mimics paho's info.rc return value.

        Delegates to _aiomqtt_publish_result_wrapper, which performs the
        actual async publish call and exception-to-rc translation.
        """
        return _aiomqtt_publish_result_wrapper(self, topic, payload)

    def close(self) -> None:
        async def _close():
            if self._cm is not None:
                await self._cm.__aexit__(None, None, None)

        asyncio.run_coroutine_threadsafe(_close(), self._loop).result()
        self._loop.call_soon_threadsafe(self._loop.stop)


def _aiomqtt_publish_result_wrapper(bridge: "_aiomqtt_sync_client_bridge", topic: str, payload: bytes) -> int:
    """Await client.publish() on the bridge's loop and derive a paho-like rc.

    paho-mqtt's client.publish() returns an MQTTMessageInfo object whose
    .rc attribute is a numeric result code (0 on success, non-zero on
    failure). aiomqtt's client.publish() is instead a coroutine that
    returns None on success and raises aiomqtt.MqttError on failure. This
    wrapper submits the publish coroutine to the bridge's background event
    loop, blocks for the result, and converts the outcome into a plain int
    (0 on success, 1 if aiomqtt.MqttError was raised) so callers written
    against paho's integer return-code convention keep working unchanged.
    """
    import aiomqtt

    async def _publish():
        await bridge.async_client.publish(topic, payload)

    future = asyncio.run_coroutine_threadsafe(_publish(), bridge._loop)
    try:
        future.result()
        return 0
    except aiomqtt.MqttError:
        return 1


def _default_client(host: str = "localhost", port: int = 1883):
    bridge = _aiomqtt_sync_client_bridge()
    bridge.connect(host, port)
    return bridge


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        rc = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return rc
