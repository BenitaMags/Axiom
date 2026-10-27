"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import json
import asyncio


class _AiomqttSyncBridgeClient:
    """Synchronous-looking facade around an aiomqtt.Client.

    paho-mqtt's ``mqtt.Client()`` gives a persistent, callback-driven client
    whose ``connect()`` and ``publish()`` methods are plain synchronous calls,
    and whose ``publish()`` returns an ``MQTTMessageInfo`` object exposing an
    ``.rc`` result code. aiomqtt's ``Client`` is async-only: it must be used
    as ``async with aiomqtt.Client(...) as client:``, and its ``publish()``
    coroutine returns ``None`` -- there is no result-code object at all.

    This wrapper approximates the old synchronous, persistent-connection API
    on top of aiomqtt by opening a fresh async context (via ``asyncio.run``)
    for each operation and awaiting ``publish()`` inside it, then fabricating
    a small result object with an ``.rc`` attribute (``0`` on success, ``1``
    on failure) so existing callers doing ``getattr(info, "rc", 0)`` keep
    working unchanged. This is only an approximation: unlike paho-mqtt it
    does not keep a single connection open across publishes, it reconnects
    per call.
    """

    class _Result:
        def __init__(self, rc=0):
            self.rc = rc

    def __init__(self, mqtt_module, host, port):
        self._mqtt_module = mqtt_module
        self._host = host
        self._port = port

    def connect(self, host=None, port=None):
        """Record connection parameters for later use.

        aiomqtt has no standalone ``connect()`` -- connections are opened
        per-operation via its async context manager -- so this just stores
        the (possibly updated) host/port for ``publish()`` to use.
        """
        if host is not None:
            self._host = host
        if port is not None:
            self._port = port

    def publish(self, topic, payload=None, *args, **kwargs):
        """Publish a message, blocking, and return an object with ``.rc``.

        Internally opens a short-lived aiomqtt connection via
        ``asyncio.run`` since aiomqtt only exposes an async publish
        coroutine, then fabricates the ``.rc`` result code that aiomqtt's
        ``publish()`` (which returns ``None``) does not provide.
        """

        async def _do_publish():
            async with self._mqtt_module.Client(self._host, self._port) as client:
                await client.publish(topic, payload)

        try:
            asyncio.run(_do_publish())
            return self._Result(0)
        except Exception:
            return self._Result(1)


def _aiomqtt_sync_bridge_client(mqtt_module, host, port):
    """Build and connect a sync-looking bridge client for ``mqtt_module``.

    Factory used by call sites that previously did
    ``client = mqtt.Client(); client.connect(host, port)`` against
    paho-mqtt. It instantiates ``_AiomqttSyncBridgeClient`` (see its
    docstring for the full paho-mqtt -> aiomqtt adaptation rationale) and
    calls its ``connect()`` so those call sites keep working unchanged.
    """
    client = _AiomqttSyncBridgeClient(mqtt_module, host, port)
    client.connect(host, port)
    return client


def _default_client(host: str = "localhost", port: int = 1883):
    import paho.mqtt.client as mqtt  # lazy import for testability

    return _aiomqtt_sync_bridge_client(mqtt, host, port)


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
