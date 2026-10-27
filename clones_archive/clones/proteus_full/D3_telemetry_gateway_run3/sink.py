"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import json


def _axiom_mqtt_client_factory_wrapper(host: str = "localhost", port: int = 1883):
    """Bridge paho-mqtt's synchronous client-construction pattern to aiomqtt.

    paho-mqtt: ``mqtt.Client()`` returns a plain client object immediately,
    with no network activity; the caller then calls ``client.connect(host,
    port)`` separately to perform the (blocking) handshake before using the
    client for further synchronous calls.

    aiomqtt: ``aiomqtt.Client(hostname, port)`` returns a client object whose
    connection is only actually established when its async context manager is
    entered (``async with aiomqtt.Client(...) as client:``); everything is a
    coroutine, and there is no plain constructor + separate synchronous
    connect step.

    This wrapper constructs the ``aiomqtt.Client`` and immediately drives its
    ``__aenter__`` (via ``_axiom_mqtt_connect_wrapper``, using
    ``asyncio.run``) so a "connected" client object can still be returned
    synchronously from a factory function, matching the old
    construct-then-connect contract. Note this only approximates the original
    behavior: aiomqtt models the connection as a context-managed session
    (paired ``__aenter__``/``__aexit__``) rather than a simple stored client
    handle, so lifecycle management (e.g. clean disconnect, reconnection)
    no longer matches the original semantics exactly.
    """
    import aiomqtt

    client = aiomqtt.Client(host, port)
    _axiom_mqtt_connect_wrapper(client)
    return client


def _axiom_mqtt_connect_wrapper(client):
    """Bridge aiomqtt's coroutine-based connection entry point to sync code.

    paho-mqtt: ``client.connect(host, port)`` synchronously opens the socket
    and completes the MQTT CONNECT handshake before returning, using a client
    object that was already constructed separately.

    aiomqtt: there is no standalone synchronous ``connect`` method; the
    connection is instead established by awaiting ``client.__aenter__()``
    (normally invoked implicitly by ``async with aiomqtt.Client(...) as
    client:``).

    This wrapper runs ``client.__aenter__()`` to completion via
    ``asyncio.run()``, so call sites written against a synchronous
    ``connect()`` call keep working unchanged. This changes reconnection
    semantics: aiomqtt treats a connection as a context-managed session
    entered/exited in pairs, rather than an independently reconnectable
    ``connect()`` call, so behavior after a dropped connection is not
    equivalent to the original paho-mqtt semantics.
    """
    import asyncio

    return asyncio.run(client.__aenter__())


def _axiom_mqtt_publish_wrapper(client, topic: str, payload: bytes) -> int:
    """Bridge aiomqtt's coroutine-based publish to a synchronous rc-style int.

    paho-mqtt: ``client.publish(topic, payload)`` returns a
    ``MQTTMessageInfo`` object synchronously, whose ``.rc`` attribute is 0 on
    success and nonzero on failure.

    aiomqtt: ``client.publish(topic, payload)`` is a coroutine that returns
    ``None`` on success and raises ``aiomqtt.MqttError`` on failure -- there
    is no rc-carrying return value at all.

    This wrapper awaits the publish coroutine via ``asyncio.run()`` and
    catches ``aiomqtt.MqttError``, synthesizing an rc-like integer (0 on
    success, 1 on failure) so that callers relying on the existing
    ``int`` return contract of ``MqttSink.publish`` keep working unchanged.
    """
    import asyncio

    import aiomqtt

    async def _do_publish():
        await client.publish(topic, payload)

    try:
        asyncio.run(_do_publish())
        return 0
    except aiomqtt.MqttError:
        return 1


def _default_client(host: str = "localhost", port: int = 1883):
    import paho.mqtt.client as mqtt  # lazy import for testability

    client = _axiom_mqtt_client_factory_wrapper(host, port)
    return client


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        rc = _axiom_mqtt_publish_wrapper(self.client, f"{self.topic_base}/{tag}", payload)
        return rc
