"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import json
import asyncio
import threading


class _aiomqtt_sync_client_wrapper:
    """Synchronous-looking facade over an aiomqtt.Client connection.

    paho-mqtt's ``mqtt.Client()`` yields an object with blocking-style
    ``.connect(host, port)`` and ``.publish(topic, payload)`` methods; the
    latter returns an ``MQTTMessageInfo`` object whose ``.rc`` attribute
    holds paho's numeric result code (``0`` means success). aiomqtt has no
    equivalent persistent, synchronous client: instead, you open
    ``aiomqtt.Client(hostname, port)`` as an ``async with`` context manager
    and ``await client.publish(...)``, which returns ``None`` on success
    and raises an exception on failure -- there is no ``rc`` at all.

    This wrapper starts a private asyncio event loop on a background
    thread, keeps the aiomqtt ``async with`` context open for the lifetime
    of the wrapper (opened lazily by ``.connect()``), and exposes
    ``.connect()``/``.publish()`` methods that submit coroutines to that
    loop via ``asyncio.run_coroutine_threadsafe`` and block on the result.
    Because aiomqtt's ``publish`` carries no result code, we fabricate a
    tiny stand-in object with an ``.rc`` attribute (``0`` on success, ``1``
    if an exception was raised) so call sites written against paho's
    ``info.rc`` convention keep working unchanged. This is a best-effort
    bridge only -- it does not faithfully reproduce paho's non-blocking,
    callback-driven, auto-reconnecting semantics.
    """

    class _Info:
        __slots__ = ("rc",)

        def __init__(self, rc: int = 0):
            self.rc = rc

    def __init__(self):
        self._aiomqtt = None
        self._host = None
        self._port = None
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._client = None
        self._cm = None

    def connect(self, host: str = "localhost", port: int = 1883):
        """Open (and keep open) the underlying aiomqtt.Client connection."""
        import aiomqtt  # local import: keeps the module-level import untouched

        self._aiomqtt = aiomqtt
        self._host = host
        self._port = port

        async def _open():
            self._cm = self._aiomqtt.Client(self._host, self._port)
            self._client = await self._cm.__aenter__()

        future = asyncio.run_coroutine_threadsafe(_open(), self._loop)
        future.result()
        return self._Info(0)

    def publish(self, topic: str, payload: bytes):
        """Publish via aiomqtt and fabricate a paho-like rc result object."""

        async def _pub():
            await self._client.publish(topic, payload)

        future = asyncio.run_coroutine_threadsafe(_pub(), self._loop)
        try:
            future.result()
            return self._Info(0)
        except Exception:
            return self._Info(1)


def _default_client(host: str = "localhost", port: int = 1883):
    client = _aiomqtt_sync_client_wrapper()
    client.connect(host, port)
    return client


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
