"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import asyncio
import json


class _AxiomSyncMqttClient:
    """Synchronous facade around an amqtt.client.MQTTClient connection.

    paho-mqtt's `mqtt.Client()` is a plain synchronous object: calling
    `client.connect(host, port)` blocks the calling thread until the TCP/MQTT
    handshake finishes, and `client.publish(topic, payload)` returns a
    `MQTTMessageInfo` object whose `.rc` attribute (0 == success) is read
    immediately, with no event loop involved anywhere.

    amqtt's `MQTTClient` is asyncio-native: both `connect(uri)` and
    `publish(topic, payload)` are coroutines that must be awaited from an
    event loop, and `publish()` resolves to `None` on success / raises on
    failure instead of returning an object with an `.rc` return code.

    This facade owns a private asyncio event loop and drives amqtt's
    coroutines to completion with `loop.run_until_complete(...)`, exposing
    plain synchronous `connect()`/`publish()` methods so existing callers
    written against paho's blocking API keep working unchanged. `publish()`
    translates amqtt's "None or raise" outcome back into a small object with
    an `.rc` attribute (0 on success, 1 on failure) to preserve the contract
    relied on by `MqttSink.publish`.
    """

    def __init__(self, host: str = "localhost", port: int = 1883):
        import amqtt.client as mqtt  # amqtt's asyncio-based MQTT client

        self._loop = asyncio.new_event_loop()
        self._client = mqtt.MQTTClient()
        self._loop.run_until_complete(self._client.connect(f"mqtt://{host}:{port}"))

    def publish(self, topic: str, payload: bytes):
        class _Result:
            rc = 0

        result = _Result()
        try:
            self._loop.run_until_complete(self._client.publish(topic, payload))
            result.rc = 0
        except Exception:
            result.rc = 1
        return result

    def disconnect(self):
        try:
            self._loop.run_until_complete(self._client.disconnect())
        finally:
            self._loop.close()


def _axiom_mqtt_client_factory_wrapper(host: str = "localhost", port: int = 1883):
    """Bridge paho-mqtt's `mqtt.Client()` + `client.connect(host, port)`.

    paho-mqtt: `mqtt.Client()` constructs a client synchronously and
    `client.connect(host, port)` blocks until connected; the returned object
    supports synchronous `.publish(...)` calls returning `.rc`-bearing
    results.

    amqtt: there is no synchronous constructor/connect pair -- `MQTTClient()`
    must be connected via `await client.connect(uri)` on an asyncio event
    loop, and `publish()` is itself a coroutine returning `None` rather than
    an `.rc` code.

    This wrapper hides that asyncio requirement by returning an
    `_AxiomSyncMqttClient`, which owns its own event loop and drives amqtt's
    connect/publish coroutines synchronously, so call sites that expect a
    blocking, paho-style client object keep working without becoming async.
    """
    return _AxiomSyncMqttClient(host=host, port=port)


def _default_client(host: str = "localhost", port: int = 1883):
    return _axiom_mqtt_client_factory_wrapper(host, port)


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
