"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import json
import asyncio


def _axiom_run_coro(coro):
    """Run an amqtt coroutine to completion regardless of whether an asyncio event
    loop is already active in this thread.

    amqtt's client methods (connect/publish/etc.) are coroutines and must be
    awaited inside an event loop, whereas paho-mqtt's equivalent calls are plain
    synchronous functions. This helper bridges that gap: if no loop is running it
    uses asyncio.run() to create one, otherwise it drives the coroutine to
    completion on the existing loop via run_until_complete().
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    else:
        return loop.run_until_complete(coro)


def _axiom_mqtt_client_factory_wrapper():
    """paho-mqtt's mqtt.Client() constructs a synchronous client object that is
    immediately usable for connect()/publish() calls from ordinary (non-async)
    code. amqtt instead exposes amqtt.client.MQTTClient, whose connect()/publish()
    methods are coroutines that must be awaited inside an asyncio event loop.

    This wrapper constructs and returns an MQTTClient instance unchanged; it
    cannot make the resulting object behave synchronously on its own -- callers
    must route connect/publish operations through the other wrapper functions
    below, which bridge the async calls back to a synchronous call boundary.
    """
    from amqtt.client import MQTTClient

    return MQTTClient()


def _axiom_mqtt_connect_wrapper(client, host, port):
    """paho-mqtt's client.connect(host, port) takes separate host/port arguments
    and blocks synchronously, returning None. amqtt's client.connect() instead
    takes a single broker URI (e.g. "mqtt://host:port") and is a coroutine.

    This wrapper builds the URI from the given host/port and drives the
    resulting coroutine to completion via _axiom_run_coro(), bridging the
    async connect() call back to the synchronous call site.
    """
    uri = f"mqtt://{host}:{port}"
    return _axiom_run_coro(client.connect(uri))


def _axiom_mqtt_publish_wrapper(client, topic, payload):
    """paho-mqtt's client.publish(topic, payload) returns a MQTTMessageInfo
    object exposing an 'rc' attribute (0 on success) that callers inspect
    synchronously. amqtt's client.publish(topic, payload) is a coroutine with
    no return code at all.

    This wrapper runs the publish coroutine to completion via _axiom_run_coro()
    and synthesizes a paho-style rc integer: 0 if the coroutine completed
    without raising, or 1 if it raised an exception. This preserves the
    int-returning contract expected by MqttSink.publish(), though note that
    truly non-blocking behavior would require MqttSink.publish() itself to
    become async.
    """
    try:
        _axiom_run_coro(client.publish(topic, payload))
        return 0
    except Exception:
        return 1


def _default_client(host: str = "localhost", port: int = 1883):
    import paho.mqtt.client as mqtt  # lazy import for testability

    client = _axiom_mqtt_client_factory_wrapper()
    _axiom_mqtt_connect_wrapper(client, host, port)
    return client


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        rc = _axiom_mqtt_publish_wrapper(self.client, f"{self.topic_base}/{tag}", payload)
        return rc
