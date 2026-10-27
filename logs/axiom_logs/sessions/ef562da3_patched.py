"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import asyncio
import json


class _SyncPublishFacade:
    """Synchronous-looking facade around an amqtt ``MQTTClient``.

    paho-mqtt's ``client.publish(topic, payload)`` is synchronous and
    returns a ``MQTTMessageInfo`` object with an ``.rc`` attribute (0 means
    the publish was accepted). amqtt's ``MQTTClient.publish(topic, payload)``
    is a coroutine: it must be awaited, and on success it returns nothing
    useful to inspect for an rc-style status -- failures are instead raised
    as exceptions.

    This facade drives the amqtt publish coroutine to completion on the
    event loop captured at connect time, and on success returns a tiny
    object exposing ``.rc == 0`` so existing callers written against
    paho-mqtt's ``getattr(info, "rc", 0)`` convention keep working
    unchanged. Any exception raised while awaiting amqtt's ``publish()``
    (e.g. for a bad topic) is left to propagate to the caller exactly as
    paho-mqtt's own ``publish()`` would have raised for the equivalent
    failure -- it is never caught and converted into a return value here.
    """

    class _Info:
        __slots__ = ("rc",)

        def __init__(self, rc: int) -> None:
            self.rc = rc

    def __init__(self, client, loop):
        self._client = client
        self._loop = loop

    def publish(self, topic: str, payload: bytes):
        self._loop.run_until_complete(self._client.publish(topic, payload))
        return self._Info(0)


def _amqtt_client_factory_wrapper(host: str = "localhost", port: int = 1883):
    """Bridge paho-mqtt's synchronous ``mqtt.Client()`` + ``client.connect()``
    construction pattern to amqtt's async ``MQTTClient``.

    paho-mqtt's ``mqtt.Client()`` returns an object that is immediately
    usable for synchronous ``.connect(host, port)`` / ``.publish(...)``
    calls. amqtt's ``MQTTClient`` instead exposes coroutine methods
    (``connect(uri)``, ``publish(topic, payload)``) that must be run on an
    asyncio event loop.

    This wrapper constructs the amqtt client, drives its ``connect(uri)``
    coroutine to completion on an event loop so that -- from the caller's
    perspective -- construction remains synchronous just like paho-mqtt's,
    and returns a ``_SyncPublishFacade`` whose synchronous-looking
    ``.publish()`` method internally awaits amqtt's ``publish()`` coroutine
    on that same loop. This lets ``MqttSink`` keep calling
    ``self.client.publish(...)`` without itself becoming async.
    """
    from amqtt.client import MQTTClient  # lazy import for testability

    loop = asyncio.get_event_loop()
    client = MQTTClient()
    uri = f"mqtt://{host}:{port}/"
    loop.run_until_complete(client.connect(uri))
    return _SyncPublishFacade(client, loop)


def _default_client(host: str = "localhost", port: int = 1883):
    return _amqtt_client_factory_wrapper(host, port)


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
