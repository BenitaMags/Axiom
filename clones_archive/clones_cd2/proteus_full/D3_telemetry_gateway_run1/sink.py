"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import json


def _aiomqtt_sync_client_wrapper(host: str = "localhost", port: int = 1883):
    """Bridge paho-mqtt's synchronous ``Client`` API to aiomqtt's async API.

    paho-mqtt's ``Client.connect()`` is an ordinary blocking call that
    returns an int result code, and ``Client.publish()`` is an ordinary
    blocking call that returns an ``MQTTMessageInfo`` object whose ``.rc``
    attribute holds an int result code. aiomqtt's ``Client`` has no such
    synchronous calls at all: it is an async context manager, so
    "connecting" means awaiting ``__aenter__``/``__aexit__``, and
    ``publish()`` is a coroutine that returns ``None`` on success and
    raises ``aiomqtt.MqttError`` on failure.

    This wrapper owns a private asyncio event loop and an aiomqtt
    ``Client`` instance. It drives ``__aenter__`` to completion once (to
    establish the connection) and exposes synchronous ``connect()``,
    ``publish()``, and ``disconnect()`` methods that run the
    corresponding coroutines to completion via that loop, translating the
    ``None``-or-exception outcome back into a small object with an
    ``rc`` attribute (0 for success, 1 for failure) so that existing
    synchronous call sites -- written against paho-mqtt's return-code
    convention -- keep working unmodified.
    """
    import asyncio
    import aiomqtt

    class _RcResult:
        def __init__(self, rc: int = 0):
            self.rc = rc

    class _SyncClientAdapter:
        def __init__(self, host: str, port: int):
            self._loop = asyncio.new_event_loop()
            self._client = aiomqtt.Client(host, port)
            self._entered = False

        def connect(self, *_args, **_kwargs) -> int:
            if not self._entered:
                self._loop.run_until_complete(self._client.__aenter__())
                self._entered = True
            return 0

        def publish(self, topic, payload=None, *args, **kwargs) -> "_RcResult":
            try:
                self._loop.run_until_complete(self._client.publish(topic, payload))
            except aiomqtt.MqttError:
                return _RcResult(1)
            return _RcResult(0)

        def disconnect(self) -> None:
            if self._entered:
                self._loop.run_until_complete(
                    self._client.__aexit__(None, None, None)
                )
                self._entered = False
            self._loop.close()

    adapter = _SyncClientAdapter(host, port)
    adapter.connect()
    return adapter


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
