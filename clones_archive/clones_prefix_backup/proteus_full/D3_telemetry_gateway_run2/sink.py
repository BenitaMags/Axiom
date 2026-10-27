"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import asyncio
import json


class _AiomqttSyncClientWrapper:
    """Synchronous facade around ``aiomqtt.Client``.

    paho-mqtt's ``mqtt.Client()`` exposes synchronous ``connect()`` and
    ``publish()`` methods; ``publish()`` returns an ``MQTTMessageInfo``-like
    object with an ``.rc`` attribute holding the paho result code (0 means
    success). aiomqtt's ``Client`` is instead an async context manager:
    connecting and publishing are coroutines that must run inside an event
    loop, and a successful ``publish()`` call returns ``None`` -- failures
    are signalled by raising ``aiomqtt.MqttError`` rather than by a nonzero
    result code.

    This wrapper stores the connection parameters and, on each synchronous
    ``publish()`` call, drives a short-lived aiomqtt session to completion
    via ``asyncio.run()``, translating the coroutine's outcome (success or
    raised exception) into an object exposing an ``.rc`` attribute (0 for
    success, 1 for failure). That lets existing paho-style call sites, which
    read ``getattr(info, "rc", 0)``, keep working unchanged even though the
    underlying client is now async and connection-per-call rather than a
    single persistent connection.
    """

    class _Result:
        def __init__(self, rc: int):
            self.rc = rc

    def __init__(self, host: str = "localhost", port: int = 1883):
        self._host = host
        self._port = port

    def connect(self, host=None, port=None):
        """Record connection parameters for later use.

        paho-mqtt's ``connect()`` opens a persistent socket immediately.
        aiomqtt only connects inside an ``async with aiomqtt.Client(...)``
        block, so here we just remember the target host/port; the actual
        connection is opened (and closed) around each ``publish()`` call.
        """
        if host is not None:
            self._host = host
        if port is not None:
            self._port = port

    def publish(self, topic: str, payload: bytes):
        """Publish ``payload`` to ``topic`` using a short-lived aiomqtt session.

        Runs an async aiomqtt connect+publish to completion via
        ``asyncio.run()`` and converts the result into a paho-like object
        with an ``.rc`` attribute, since aiomqtt returns ``None`` on success
        and raises on failure instead of returning a result code.
        """
        import aiomqtt  # lazy import for testability

        async def _do_publish():
            async with aiomqtt.Client(self._host, self._port) as client:
                await client.publish(topic, payload)

        try:
            asyncio.run(_do_publish())
            return self._Result(0)
        except Exception:
            return self._Result(1)


def _aiomqtt_sync_client_wrapper(host: str = "localhost", port: int = 1883):
    """Return a synchronous-facade client backed by aiomqtt.

    Mirrors the previous paho-mqtt-based ``_default_client`` behavior of
    returning a client object with synchronous ``connect()``/``publish()``
    methods, while internally delegating to aiomqtt's async API through
    ``_AiomqttSyncClientWrapper``.
    """
    client = _AiomqttSyncClientWrapper(host, port)
    client.connect(host, port)
    return client


def _default_client(host: str = "localhost", port: int = 1883):
    import paho.mqtt.client as mqtt  # lazy import for testability

    client = _aiomqtt_sync_client_wrapper(host, port)
    return client


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
