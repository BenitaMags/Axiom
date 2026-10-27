"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import asyncio
import json


class _AxiomMqttClientWrapper:
    """Synchronous adapter that fakes paho-mqtt's client semantics on top of aiomqtt.

    paho-mqtt's `Client.connect()` blocks until a connection is established and
    `Client.publish()` blocks until the publish is queued/sent, returning a
    `MessageInfo` object whose `.rc` attribute is a numeric result code (0 for
    success). aiomqtt instead exposes an async `Client` that must be used as an
    `async with` context manager, and its `publish()` coroutine returns `None`
    on success (raising an exception on failure) rather than a result-code
    object.

    To bridge this without turning every caller into async code, this wrapper
    stores the connection target on "connect" (a no-op, since aiomqtt has no
    separate connect step) and, on each "publish", opens a short-lived aiomqtt
    session via `asyncio.run(...)`, performs the publish, and synthesizes a
    small object with an `.rc` attribute (0 on success, 1 if an exception was
    raised) so existing `getattr(info, "rc", 0)` call sites keep working
    unchanged.
    """

    def __init__(self, host: str = "localhost", port: int = 1883):
        self._host = host
        self._port = port

    def connect(self, host: str, port: int) -> None:
        """No-op placeholder: aiomqtt connects per-operation via `async with`."""
        self._host = host
        self._port = port

    def publish(self, topic: str, payload: bytes):
        """Publish `payload` to `topic` by running a one-shot aiomqtt session.

        Returns an object exposing `.rc` (0 on success, 1 on failure) to mimic
        paho-mqtt's `MessageInfo.rc` for compatibility with existing callers.
        """
        import aiomqtt  # lazy import for testability

        async def _do_publish():
            async with aiomqtt.Client(self._host, self._port) as client:
                await client.publish(topic, payload)

        class _Result:
            rc = 0

        try:
            asyncio.run(_do_publish())
        except Exception:
            _Result.rc = 1
        return _Result()


def _axiom_mqtt_client_wrapper(host: str = "localhost", port: int = 1883):
    """Build a synchronous-looking MQTT client backed by aiomqtt.

    aiomqtt has no synchronous, stateful `Client()` constructor followed by a
    blocking `.connect()`/`.publish()` pair like paho-mqtt does; instead it is
    used as an async context manager per operation. This factory returns a
    `_AxiomMqttClientWrapper` instance that mimics the paho-mqtt client's
    `connect()`/`publish()` surface while internally driving aiomqtt through
    short-lived async sessions, so `_default_client()`'s existing synchronous
    call pattern does not need to change.
    """
    client = _AxiomMqttClientWrapper(host, port)
    client.connect(host, port)
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
