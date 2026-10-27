"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import json


def _aiomqtt_sync_client_wrapper(host: str = "localhost", port: int = 1883):
    """Bridge aiomqtt's async, context-managed `Client` to the synchronous,
    callback-free `connect()`/`publish()` interface this module expects.

    paho-mqtt's `Client.connect()` / `Client.publish()` are plain synchronous
    calls; `publish()` returns an `MQTTMessageInfo` object exposing an `.rc`
    result code that callers inspect directly. aiomqtt has no such
    synchronous surface at all: a connection only exists for the lifetime of
    an `async with aiomqtt.Client(...) as client:` block, and `client.publish()`
    is a coroutine that returns `None` on success and raises an
    `aiomqtt.MqttError` on failure instead of returning a code.

    This wrapper returns a small object whose `.publish(topic, payload)`
    method opens a short-lived aiomqtt connection, awaits the publish via
    `asyncio.run(...)` (so callers keep calling it synchronously), and maps
    the "no exception raised" success case back onto an object with an
    `.rc` attribute of 0, matching what callers of the old paho-mqtt
    `MQTTMessageInfo.rc` expect to read.
    """
    import asyncio

    import aiomqtt

    class _PublishResult:
        rc = 0

    class _SyncClientWrapper:
        def __init__(self, host: str, port: int):
            self._host = host
            self._port = port

        def publish(self, topic: str, payload: bytes):
            async def _do_publish():
                async with aiomqtt.Client(self._host, self._port) as client:
                    await client.publish(topic, payload)

            asyncio.run(_do_publish())
            return _PublishResult()

    return _SyncClientWrapper(host, port)


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
