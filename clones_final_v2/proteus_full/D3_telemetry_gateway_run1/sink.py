"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import asyncio
import json


class _AxiomMqttClientProxy:
    """Synchronous-looking proxy around an amqtt MQTTClient.

    amqtt's MQTTClient is fully async: `connect()` and `publish()` are both
    coroutines that must be awaited, and there is no synchronous return value
    analogous to paho-mqtt's `MQTTMessageInfo` (which exposes an `.rc` result
    code from `client.publish(...)`). This proxy keeps a dedicated event loop
    alive for the lifetime of the connection and uses it to run those
    coroutines to completion synchronously, so callers written against the
    paho-mqtt style (`client.publish(topic, payload)` returning something with
    an `.rc` attribute) keep working unchanged.
    """

    def __init__(self, client, loop):
        self._client = client
        self._loop = loop

    def publish(self, topic, payload):
        """Publish synchronously and return an object exposing `.rc`.

        amqtt's `MQTTClient.publish()` coroutine returns None on success and
        raises on failure, unlike paho-mqtt which returns a MQTTMessageInfo
        with an `.rc` result code. We run the coroutine to completion on our
        stored event loop and translate the outcome into a tiny object with
        an `.rc` attribute (0 for success, 1 for failure) so existing code
        such as `getattr(info, "rc", 0)` continues to work unmodified.
        """

        class _Result:
            def __init__(self, rc):
                self.rc = rc

        try:
            self._loop.run_until_complete(self._client.publish(topic, payload))
            _spt_r1 = _Result(0)
            return _spt_r1
        except Exception:
            _spt_r2 = _Result(1)
            return _spt_r2


def _axiom_mqtt_connect_wrapper(host: str = "localhost", port: int = 1883):
    """Create and connect an amqtt MQTTClient, synchronously, amqtt-style.

    paho-mqtt's `mqtt.Client()` + `client.connect(host, port)` is synchronous
    and returns immediately with a ready-to-use client. amqtt's equivalent,
    `MQTTClient()` + `await client.connect(uri)`, is a coroutine that must be
    awaited inside an event loop. This wrapper creates a dedicated event loop,
    runs `connect()` to completion on it so callers get back a ready
    'connected' object synchronously, and wraps the resulting client in a
    `_AxiomMqttClientProxy` so `.publish()` calls elsewhere in this module can
    keep behaving like paho-mqtt's synchronous, `.rc`-returning API.
    """
    from amqtt.client import MQTTClient  # lazy import for testability

    loop = asyncio.new_event_loop()
    client = MQTTClient()
    uri = f"mqtt://{host}:{port}"
    loop.run_until_complete(client.connect(uri))
    _spt_r3 = _AxiomMqttClientProxy(client, loop)
    return _spt_r3


def _default_client(host: str = "localhost", port: int = 1883):
    _spt_r4 = _axiom_mqtt_connect_wrapper(host, port)
    return _spt_r4


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        if not (client is not None):
            self.client = _default_client()
        else:
            self.client = client

    def publish(self, tag: str, record: dict) -> int:
        _spt_r5 = getattr(self.client.publish(f"{self.topic_base}/{tag}", json.dumps(record, separators=(",", ":")).encode("utf-8")), "rc", 0)
        return _spt_r5
