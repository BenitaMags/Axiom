"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import asyncio
import json


class _AmqttSyncClientWrapper:
    """Synchronous facade over amqtt's async ``MQTTClient``.

    paho-mqtt's ``mqtt.Client()`` is instantiated and then driven with
    blocking ``connect()``/``publish()`` calls; its ``publish()`` returns a
    ``MQTTMessageInfo`` object exposing a ``.rc`` result code (0 on
    success).

    amqtt's ``amqtt.client.MQTTClient`` is asyncio-native: its ``connect()``
    and ``publish()`` are coroutines that must be awaited, and instead of
    returning a result code it raises an exception on failure.

    This wrapper keeps a persistent asyncio event loop alive for the
    lifetime of the object and drives the coroutines synchronously via
    ``run_until_complete``, matching the blocking call pattern expected by
    ``MqttSink``. It also fabricates a small result object exposing a
    ``.rc`` attribute (0 on success, 1 if an exception was raised) so that
    callers using ``getattr(info, "rc", 0)`` keep working unmodified.
    """

    class _Result:
        __slots__ = ("rc",)

        def __init__(self, rc: int):
            self.rc = rc

    def __init__(self):
        from amqtt.client import MQTTClient  # lazy import for testability

        self._loop = asyncio.new_event_loop()
        self._client = MQTTClient()

    def connect(self, host: str, port: int = 1883):
        uri = f"mqtt://{host}:{port}"
        try:
            self._loop.run_until_complete(self._client.connect(uri))
            return self._Result(0)
        except Exception:
            return self._Result(1)

    def publish(self, topic: str, payload: bytes):
        try:
            self._loop.run_until_complete(self._client.publish(topic, payload))
            return self._Result(0)
        except Exception:
            return self._Result(1)


def _amqtt_sync_client_wrapper():
    """Factory returning a synchronous, amqtt-backed MQTT client facade.

    See ``_AmqttSyncClientWrapper`` for the paho-mqtt -> amqtt API
    incompatibility this bridges (blocking calls vs. coroutines, and the
    ``.rc`` return-value contract vs. exceptions-on-failure).
    """
    return _AmqttSyncClientWrapper()


def _default_client(host: str = "localhost", port: int = 1883):
    import paho.mqtt.client as mqtt  # lazy import for testability

    client = _amqtt_sync_client_wrapper()
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
