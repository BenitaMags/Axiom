"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import json


def _aiomqtt_sync_client_wrapper(host: str = "localhost", port: int = 1883):
    """Bridge paho-mqtt's synchronous client API onto aiomqtt's async one.

    paho-mqtt's ``mqtt.Client()`` gives back a synchronous object with
    blocking ``.connect(host, port)`` and ``.publish(topic, payload)``
    methods; ``.publish`` returns an ``MQTTMessageInfo`` whose ``.rc``
    attribute is the paho return code (0 == success).

    aiomqtt has no such synchronous object: connecting is done by entering
    an async context manager (``async with aiomqtt.Client(host, port) as
    client``), and publishing is an awaitable ``client.publish(topic,
    payload)`` that returns ``None`` and signals failure by raising an
    exception rather than by returning a return code.

    This wrapper opens one dedicated asyncio event loop, uses it to enter
    the aiomqtt client's async context once (so the connection is
    established immediately, mirroring paho-mqtt's synchronous ``.connect``
    semantics), and returns a small shim object exposing synchronous-looking
    ``.connect()`` (a no-op, since the connection already happened) and
    ``.publish()`` methods. ``.publish()`` runs the underlying coroutine to
    completion on the same loop and, on success, returns an object with an
    ``.rc`` attribute set to 0 -- reproducing paho-mqtt's rc-return
    convention for the rest of this module, which was written against that
    convention.
    """
    import asyncio
    import aiomqtt

    loop = asyncio.new_event_loop()

    class _FakePublishInfo:
        def __init__(self, rc):
            self.rc = rc

    class _SyncClientShim:
        def __init__(self, aiomqtt_client):
            self._client = aiomqtt_client

        def connect(self, *args, **kwargs):
            # Connection was already established when the aiomqtt client's
            # async context was entered during wrapper construction.
            return None

        def publish(self, topic, payload):
            loop.run_until_complete(self._client.publish(topic, payload))
            return _FakePublishInfo(0)

    async def _open():
        aiomqtt_client = aiomqtt.Client(host, port)
        await aiomqtt_client.__aenter__()
        return aiomqtt_client

    opened_client = loop.run_until_complete(_open())
    return _SyncClientShim(opened_client)


def _default_client(host: str = "localhost", port: int = 1883):
    import paho.mqtt.client as mqtt  # lazy import for testability

    client = _aiomqtt_sync_client_wrapper(host, port)
    client.connect(host, port)
    return client


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
