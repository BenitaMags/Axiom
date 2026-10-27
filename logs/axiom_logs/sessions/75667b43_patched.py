"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import asyncio
import json


def _amqtt_client_wrapper():
    """Create an amqtt MQTTClient standing in for paho-mqtt's mqtt.Client().

    paho-mqtt's `mqtt.Client()` immediately returns a ready-to-use,
    synchronous client object. amqtt's `MQTTClient` fills the same role but
    is asyncio-based: every network operation (connect, publish, ...) is a
    coroutine that must be scheduled on an event loop instead of being
    called directly. This wrapper simply constructs the amqtt client so the
    other wrappers below can drive its coroutines through a private
    asyncio event-loop runner (`asyncio.run`) while presenting the same
    "construct a client object" step that paho-mqtt's callers expect.
    """
    from amqtt.client import MQTTClient

    return MQTTClient()


def _amqtt_connect_wrapper(client, host, port):
    """Synchronously connect an amqtt client, matching paho's connect(host, port).

    paho-mqtt's `client.connect(host, port)` blocks the calling thread until
    the TCP/MQTT handshake completes (or raises on failure), taking the
    broker host and port as separate arguments. amqtt's equivalent is the
    coroutine `client.connect(uri)`, which instead takes a single
    'scheme://host:port' URI. This wrapper builds that URI from the
    host/port arguments and drives the coroutine to completion with
    `asyncio.run()`, so callers written against paho's synchronous
    connect(host, port) signature keep working unchanged. Any exception
    raised by amqtt's connect coroutine propagates unchanged.
    """
    uri = f"mqtt://{host}:{port}"
    asyncio.run(client.connect(uri))


def _amqtt_publish_wrapper(client, topic, payload, qos=0):
    """Publish via amqtt (or a synchronous stand-in), matching paho's
    publish()->rc contract.

    paho-mqtt's `client.publish(topic, payload)` returns a `MQTTMessageInfo`
    whose `.rc` attribute is `0` when the message was successfully queued,
    and it raises synchronously (e.g. `ValueError`) for outright misuse such
    as an invalid topic. amqtt's `client.publish(topic, payload, qos)` is a
    coroutine that raises on failure instead of returning a status object.

    Because callers may inject either a real amqtt client or a plain
    synchronous test double (matching paho's original 2-arg contract), this
    wrapper inspects the injected client's `publish` method with
    `inspect.iscoroutinefunction`. When it is a coroutine function (a genuine
    amqtt client), the wrapper drives it to completion via `asyncio.run()`,
    passing the qos argument. Otherwise it falls back to calling
    `client.publish(topic, payload)` synchronously, exactly as paho-mqtt
    callers would. In both cases it normalizes the result to the int `0` on
    success, so callers keep the int-return contract `MqttSink.publish`
    relies on. It deliberately does NOT catch or translate exceptions: any
    failure the underlying client raises propagates to the caller exactly as
    paho-mqtt's own synchronous errors would have, so callers cannot
    silently lose a publish failure.
    """
    import inspect

    if inspect.iscoroutinefunction(client.publish):
        asyncio.run(client.publish(topic, payload, qos))
    else:
        client.publish(topic, payload)
    return 0


def _default_client(host: str = "localhost", port: int = 1883):

    client = _amqtt_client_wrapper()
    _amqtt_connect_wrapper(client, host, port)
    return client


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        return _amqtt_publish_wrapper(
            self.client, f"{self.topic_base}/{tag}", payload
        )
