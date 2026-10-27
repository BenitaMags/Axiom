"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import asyncio
import json


def _axiom_mqtt_client_factory_wrapper(host: str = "localhost", port: int = 1883):
    """Bridge for paho-mqtt's ``mqtt.Client()`` + ``client.connect(host, port)``.

    paho-mqtt: ``mqtt.Client()`` returns a client object that is then
    explicitly, synchronously connected via ``client.connect(host, port)``.
    Once connected it stays connected and can be stashed on ``self.client``
    for later synchronous ``client.publish(...)`` calls.

    aiomqtt: ``aiomqtt.Client(hostname, port)`` does not connect on
    construction at all -- it is only usable as an async context manager
    (``async with aiomqtt.Client(...) as client:``), whose ``__aenter__``
    performs the connection and ``__aexit__`` disconnects. There is no
    long-lived, already-connected handle to return synchronously the way
    paho's ``connect()`` provides one.

    Because callers of this factory expect a plain, synchronously-usable
    object back (not a coroutine or async context manager), this wrapper
    does not attempt to eagerly connect. It simply records the connection
    parameters in a small dict-like holder. The real ``aiomqtt.Client`` is
    constructed and connected (via its async context manager, for the
    duration of a single publish) inside ``_axiom_mqtt_publish_wrapper``
    below, which keeps this factory's synchronous contract intact without
    requiring the whole call chain to become async.
    """
    return {"host": host, "port": port}


def _axiom_mqtt_publish_wrapper(client, topic: str, payload: bytes) -> int:
    """Bridge for paho-mqtt's ``client.publish(topic, payload) -> MQTTMessageInfo``.

    paho-mqtt: ``client.publish(...)`` is synchronous and returns an
    ``MQTTMessageInfo`` object whose ``.rc`` attribute is ``0`` on success
    (and a non-zero MQTT error code otherwise); callers read that ``.rc``
    to learn whether the publish succeeded.

    aiomqtt: ``aiomqtt.Client.publish(...)`` is a coroutine that must be
    awaited inside an ``async with aiomqtt.Client(...) as client:`` block.
    It returns ``None`` on success and raises an exception (e.g.
    ``aiomqtt.MqttError``) on failure, instead of returning an rc code.

    This wrapper preserves the old synchronous, rc-returning call
    convention: it runs a short-lived event loop (``asyncio.run``) that
    opens an aiomqtt connection using the parameters produced by
    ``_axiom_mqtt_client_factory_wrapper``, awaits the publish, and closes
    the connection again. Any exception raised during connect/publish is
    treated as a failure and mapped to ``rc=1``, mirroring the "non-zero
    means failure" convention of the old ``MQTTMessageInfo.rc`` so that
    ``MqttSink.publish`` and its callers do not need to change.
    """
    import aiomqtt

    async def _do_publish():
        async with aiomqtt.Client(
            hostname=client["host"], port=client["port"]
        ) as connected_client:
            await connected_client.publish(topic, payload)

    try:
        asyncio.run(_do_publish())
        return 0
    except Exception:
        return 1


def _default_client(host: str = "localhost", port: int = 1883):

    return _axiom_mqtt_client_factory_wrapper(host, port)


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        return _axiom_mqtt_publish_wrapper(
            self.client, f"{self.topic_base}/{tag}", payload
        )
