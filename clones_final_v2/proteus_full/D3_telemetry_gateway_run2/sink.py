"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import asyncio
import json


def _axiom_mqtt_connect_wrapper(client, host: str = "localhost", port: int = 1883):
    """Bridge paho-mqtt's synchronous `client.connect(host, port)` to amqtt.

    paho-mqtt returns nothing useful from `connect()` -- it just schedules a
    background handshake that is driven by `loop_start()`/`loop_forever()`.
    amqtt's client instead exposes an async `client.connect(uri)` coroutine
    that must be awaited inside a running event loop and must be given a
    full "mqtt://host:port" URI rather than separate host/port arguments.

    This wrapper builds the URI amqtt expects and drives the coroutine to
    completion with `asyncio.run(...)`, giving callers a synchronous
    call-compatible surface that mirrors the old paho-mqtt behaviour as
    closely as possible without turning the whole call chain async.
    """
    _spt_r1 = asyncio.run(client.connect(f"mqtt://{host}:{port}"))
    return _spt_r1


def _axiom_mqtt_publish_wrapper(client, topic: str, payload: bytes) -> int:
    """Bridge paho-mqtt's synchronous `client.publish(topic, payload)` to amqtt.

    paho-mqtt's `publish()` returns a `MQTTMessageInfo` object whose `.rc`
    attribute is the paho result code (0 on success). amqtt's client instead
    exposes an async `client.publish(topic, message, qos)` coroutine that
    returns `None` on success and raises an exception on failure -- there is
    no `.rc`-bearing object to inspect.

    This wrapper drives the coroutine synchronously via `asyncio.run(...)`
    and translates the outcome back into the small integer return-code
    convention the rest of this module (and its callers) already expect:
    0 for success, 1 if amqtt raised an exception.
    """
    try:
        asyncio.run(client.publish(topic, payload))
        return 0
    except Exception:
        return 1


def _default_client(host: str = "localhost", port: int = 1883):
    import paho.mqtt.client as mqtt  # lazy import for testability

    client = mqtt.MQTTClient()
    _axiom_mqtt_connect_wrapper(client, host, port)
    return client


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        if not (client is not None):
            self.client = _default_client()
        else:
            self.client = client

    def publish(self, tag: str, record: dict) -> int:
        _spt_r2 = _axiom_mqtt_publish_wrapper(self.client, f"{self.topic_base}/{tag}", json.dumps(record, separators=(",", ":")).encode("utf-8"))
        return _spt_r2
