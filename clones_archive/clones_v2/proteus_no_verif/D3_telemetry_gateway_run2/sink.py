"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import json
import asyncio


class _AxiomSyncMqttFacade:
    """Synchronous facade around amqtt.client.MQTTClient.

    amqtt (unlike paho-mqtt) exposes an async API: `connect()` and
    `publish()` are coroutines that must be awaited inside an asyncio
    event loop, and on success they return `None` (or raise an exception
    on failure) rather than paho-mqtt's `MQTTMessageInfo`/int return
    codes. This facade owns a private asyncio event loop and drives it
    synchronously so callers written against paho-mqtt's blocking
    `Client.connect()`/`.publish()` API (returning an int rc) keep
    working unmodified.
    """

    def __init__(self, client):
        self._client = client
        self._loop = asyncio.new_event_loop()

    def connect(self, host: str = "localhost", port: int = 1883):
        uri = f"mqtt://{host}:{port}"
        self._loop.run_until_complete(self._client.connect(uri))
        return 0

    def publish(self, topic: str, payload: bytes):
        self._loop.run_until_complete(self._client.publish(topic, payload))
        return 0

    def __del__(self):
        try:
            self._loop.close()
        except Exception:
            pass


def _axiom_mqtt_client_wrapper(host: str = "localhost", port: int = 1883):
    """Create an amqtt MQTTClient and wrap it to look like a paho-mqtt Client.

    paho-mqtt's `mqtt.Client()` gives a synchronous object whose
    `.connect(host, port)` blocks until connected and whose
    `.publish(topic, payload)` returns an `MQTTMessageInfo` with an `.rc`
    integer result code. amqtt's `MQTTClient` is asyncio-native: both
    `connect()` and `publish()` are coroutines returning `None` on
    success (raising on failure) rather than returning a result code.
    This wrapper instantiates `amqtt.client.MQTTClient` and returns a
    `_AxiomSyncMqttFacade` around it that drives a private event loop
    internally, exposing the same synchronous `.connect()` / `.publish()`
    -> int-rc surface that `MqttSink` was written against.
    """
    from amqtt.client import MQTTClient

    client = MQTTClient()
    facade = _AxiomSyncMqttFacade(client)
    facade.connect(host, port)
    return facade


def _default_client(host: str = "localhost", port: int = 1883):

    return _axiom_mqtt_client_wrapper(host, port)


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
