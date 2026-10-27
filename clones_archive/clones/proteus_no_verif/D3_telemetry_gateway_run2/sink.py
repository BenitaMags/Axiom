"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import json


def _axiom_mqtt_client_wrapper(host: str = "localhost", port: int = 1883):
    """Bridge paho-mqtt's synchronous client construction/connect to amqtt.

    paho-mqtt's ``mqtt.Client()`` followed by ``client.connect(host, port)``
    returns a ready-to-use, already-"connected" client object synchronously,
    with no event loop required by the caller.

    amqtt's ``MQTTClient`` is asyncio-native: it is constructed with no
    host/port arguments, and connecting requires ``await client.connect(uri)``
    where ``uri`` is a ``mqtt://host:port`` string, run inside an asyncio
    event loop.

    This wrapper builds the amqtt ``MQTTClient``, assembles the ``mqtt://``
    URI from the given host/port, and drives a short-lived event loop (via
    ``asyncio.run``) to await the connection, so that callers written against
    the old synchronous API can still get back a connected client object
    without themselves becoming async.
    """
    import asyncio

    from amqtt.client import MQTTClient

    client = MQTTClient()
    uri = f"mqtt://{host}:{port}"
    asyncio.run(client.connect(uri))
    return client


def _default_client(host: str = "localhost", port: int = 1883):
    import paho.mqtt.client as mqtt  # lazy import for testability

    client = _axiom_mqtt_client_wrapper(host, port)
    return client


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
