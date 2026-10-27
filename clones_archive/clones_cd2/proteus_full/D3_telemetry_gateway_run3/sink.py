"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import asyncio
import json


class _AiomqttSyncClientWrapper:
    """Adapts aiomqtt's async ``Client`` to the synchronous, callback-style
    interface that paho-mqtt's ``Client`` used to provide.

    paho-mqtt: ``Client.connect(host, port)`` connects synchronously, and
    ``Client.publish(topic, payload)`` returns an ``MQTTMessageInfo`` object
    with an ``.rc`` attribute (the MQTT result/return code).

    aiomqtt: ``Client`` is an async context manager -- you connect by
    entering ``async with aiomqtt.Client(...) as client:`` -- and
    ``client.publish(topic, payload)`` is an ``async def`` coroutine that
    returns ``None`` (it raises an exception on failure instead of
    returning a code).

    This wrapper owns a private asyncio event loop and uses it to drive
    aiomqtt's async context-manager protocol (``__aenter__``/``__aexit__``)
    and its ``publish`` coroutine to completion synchronously, so existing
    synchronous call sites (e.g. ``client.connect(...)`` then
    ``getattr(info, "rc", 0)``) keep working unchanged. Since aiomqtt
    signals failure via exceptions rather than a return code, ``publish``
    only returns a result object at all when it succeeds, and that result
    always carries ``rc=0``.
    """

    class _Result:
        def __init__(self, rc: int = 0):
            self.rc = rc

    def __init__(self, host: str = "localhost", port: int = 1883):
        self._host = host
        self._port = port
        self._loop = asyncio.new_event_loop()
        self._client = None
        self._cm = None

    def connect(self, host: str = None, port: int = None):
        """Mirrors paho-mqtt's synchronous ``connect()``: establishes the
        connection immediately by driving aiomqtt's async context-manager
        ``__aenter__`` to completion on a private event loop."""
        import aiomqtt

        if host is not None:
            self._host = host
        if port is not None:
            self._port = port
        self._cm = aiomqtt.Client(self._host, self._port)
        self._client = self._loop.run_until_complete(self._cm.__aenter__())
        return self._Result(0)

    def publish(self, topic: str, payload: bytes):
        """Mirrors paho-mqtt's synchronous ``publish()``. Runs aiomqtt's
        async ``publish()`` coroutine to completion via the private event
        loop and returns a fake result object exposing ``.rc`` (always 0
        on success, since aiomqtt raises instead of returning an error
        code) so callers using ``getattr(info, "rc", 0)`` keep working."""
        self._loop.run_until_complete(self._client.publish(topic, payload))
        return self._Result(0)

    def close(self):
        """Tears down the aiomqtt connection by driving ``__aexit__`` to
        completion, then closes the private event loop."""
        if self._cm is not None:
            self._loop.run_until_complete(self._cm.__aexit__(None, None, None))
            self._cm = None
        self._loop.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def _aiomqtt_sync_client_wrapper(host: str = "localhost", port: int = 1883):
    """Builds an `_AiomqttSyncClientWrapper` bound to (host, port) and
    connects it immediately, standing in for the old paho-mqtt call site
    `client = mqtt.Client(); client.connect(host, port)`. Returns an object
    exposing synchronous-looking `.connect()`/`.publish()` methods backed
    by aiomqtt's async `Client`, so downstream code (e.g. `MqttSink`) needs
    no changes."""
    wrapper = _AiomqttSyncClientWrapper(host, port)
    wrapper.connect()
    return wrapper


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
