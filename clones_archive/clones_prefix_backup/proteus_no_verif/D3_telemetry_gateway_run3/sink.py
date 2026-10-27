"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import json
import asyncio


class _AxiomAiomqttSyncFacade:
    """Synchronous-looking facade wrapping aiomqtt.Client.

    paho-mqtt's ``Client.connect()`` / ``Client.publish()`` are plain
    synchronous, callback-driven calls: ``connect()`` blocks briefly and
    returns, and ``publish()`` returns immediately with an
    ``MQTTMessageInfo`` object whose ``.rc`` attribute is the paho-mqtt
    result code (0 == success). aiomqtt has no synchronous equivalent --
    ``aiomqtt.Client`` is only usable as ``async with aiomqtt.Client(...) as
    client: await client.publish(...)``, and there is no persistent,
    reusable connection object outside of that async context manager.

    This facade bridges the gap by spinning up a fresh asyncio event loop
    per operation (via ``asyncio.run``) and opening/closing an aiomqtt
    connection for that single operation, then translating the coroutine's
    outcome (``None`` on success, ``aiomqtt.MqttError`` on failure) into a
    tiny object exposing ``.rc`` (0 / 1) so existing synchronous call sites
    (e.g. ``getattr(info, "rc", 0)``) keep working unmodified. It does NOT
    reproduce paho-mqtt's persistent-connection semantics or its
    non-blocking, callback-driven delivery model -- each publish opens and
    tears down its own connection.
    """

    def __init__(self, host: str, port: int):
        self._host = host
        self._port = port

    def connect(self, host=None, port=None):
        """Record connection parameters; aiomqtt connects per-operation.

        paho-mqtt's ``connect()`` establishes a persistent connection ahead
        of time. aiomqtt has no equivalent standalone call, so this just
        stores the parameters (or updates them if given) for later use by
        ``publish()``, and returns 0 to mimic paho-mqtt's success result.
        """
        if host is not None:
            self._host = host
        if port is not None:
            self._port = port
        return 0

    def publish(self, topic: str, payload):
        """Publish ``payload`` to ``topic``, returning a paho-mqtt-like result.

        aiomqtt's ``Client.publish`` is a coroutine returning ``None`` on
        success and raising ``aiomqtt.MqttError`` on failure. paho-mqtt's
        ``Client.publish`` instead returns an ``MQTTMessageInfo`` with an
        ``.rc`` attribute (0 == success). We run the aiomqtt coroutine to
        completion on a dedicated event loop, opening a short-lived
        connection for this single publish, and translate the outcome into
        a small object exposing ``.rc`` so callers built against the
        paho-mqtt result shape continue to work.
        """
        import aiomqtt

        async def _do_publish():
            async with aiomqtt.Client(self._host, self._port) as client:
                await client.publish(topic, payload)

        class _Result:
            def __init__(self, rc):
                self.rc = rc

        try:
            asyncio.run(_do_publish())
            return _Result(0)
        except Exception:
            return _Result(1)


def _axiom_mqtt_client_wrapper(host: str = "localhost", port: int = 1883):
    """Create a synchronous-looking client facade bridging paho-mqtt to aiomqtt.

    paho-mqtt's ``mqtt.Client()`` returns an object with synchronous
    ``.connect()``/``.publish()`` methods. aiomqtt only exposes an async,
    context-managed ``aiomqtt.Client`` with no direct synchronous
    equivalent. This wrapper returns a ``_AxiomAiomqttSyncFacade`` instance
    that mimics the synchronous surface paho-mqtt callers expect
    (``.connect()`` / ``.publish()`` returning an object with ``.rc``)
    while using aiomqtt internally via a per-call event loop.
    """
    return _AxiomAiomqttSyncFacade(host, port)


def _default_client(host: str = "localhost", port: int = 1883):
    import paho.mqtt.client as mqtt  # lazy import for testability

    client = _axiom_mqtt_client_wrapper(host, port)
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
