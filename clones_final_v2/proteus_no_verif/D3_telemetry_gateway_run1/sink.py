"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import asyncio
import json

from amqtt.client import MQTTClient


class _amqtt_client_wrapper:
    """Synchronous-looking facade around amqtt's asyncio-based MQTTClient.

    paho-mqtt's ``Client.connect()`` and ``Client.publish()`` are plain
    synchronous calls: ``connect()`` blocks until the TCP/MQTT handshake
    completes, and ``publish()`` returns an ``MQTTMessageInfo`` object
    exposing an ``.rc`` return-code attribute describing whether the publish
    was queued successfully (0 == success).

    amqtt's ``MQTTClient`` is asyncio-native instead: ``connect()`` and
    ``publish()`` are coroutines that must be awaited, and rather than
    returning a status code, ``publish()`` raises an exception on failure
    and returns ``None`` on success.

    This wrapper owns a private, persistent asyncio event loop and uses it
    to drive the underlying amqtt ``MQTTClient`` coroutines synchronously,
    exposing ``.connect(host, port)`` and ``.publish(topic, payload)``
    methods that block like the paho-mqtt equivalents and return an integer
    return code (0 on success, 1 on failure), so existing synchronous call
    sites written against the old API keep working unchanged.
    """

    def __init__(self):
        self._client = MQTTClient()
        self._loop = asyncio.new_event_loop()

    def connect(self, host: str = "localhost", port: int = 1883):
        uri = f"mqtt://{host}:{port}"
        self._loop.run_until_complete(self._client.connect(uri))

    def publish(self, topic: str, payload: bytes) -> int:
        try:
            self._loop.run_until_complete(self._client.publish(topic, payload))
        except Exception:
            return 1
        return 0


def _default_client(host: str = "localhost", port: int = 1883):
    client = _amqtt_client_wrapper()
    client.connect(host, port)
    return client


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        if not (client is not None):
            self.client = _default_client()
        else:
            self.client = client

    def publish(self, tag: str, record: dict) -> int:
        _spt_r1 = self.client.publish(f"{self.topic_base}/{tag}", json.dumps(record, separators=(",", ":")).encode("utf-8"))
        return _spt_r1
