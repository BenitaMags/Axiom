"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import json
import asyncio
import threading


def _axiom_mqtt_client_wrapper(host: str = "localhost", port: int = 1883):
    """Bridge paho-mqtt's synchronous Client API to aiomqtt's async Client.

    paho-mqtt's ``mqtt.Client()`` exposes blocking, callback-style methods:
    ``.connect(host, port)`` establishes the connection synchronously, and
    ``.publish(topic, payload)`` returns a ``MQTTMessageInfo``-like object
    with an ``.rc`` attribute indicating the publish result code (0 == success).

    aiomqtt instead exposes ``aiomqtt.Client(host, port)`` as an async context
    manager: connecting requires ``await client.__aenter__()`` (or ``async
    with``), and publishing is an ``await client.publish(...)`` coroutine that
    raises on failure rather than returning a result-code object.

    This wrapper owns a private background event loop (run on a daemon
    thread) so that MqttSink's synchronous call sites can keep calling
    ``.connect()`` / ``.publish()`` unmodified. On construction it opens the
    aiomqtt async context manager via ``asyncio.run_coroutine_threadsafe``,
    emulating paho-mqtt's synchronous ``.connect()`` semantics. The returned
    facade object exposes blocking ``.connect()`` (a no-op since the context
    manager is already open) and ``.publish()`` methods; ``.publish()`` runs
    the aiomqtt coroutine on the background loop, waits for the result, and
    translates success/failure into an object with an ``.rc`` attribute (0 for
    success, 1 if the coroutine raised), for parity with MqttSink.publish()
    which reads ``getattr(info, "rc", 0)``.
    """
    import aiomqtt

    class _Result:
        def __init__(self, rc: int = 0):
            self.rc = rc

    class _SyncClientFacade:
        def __init__(self, host: str, port: int):
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
            self._thread.start()
            self._client_cm = aiomqtt.Client(host, port)
            fut = asyncio.run_coroutine_threadsafe(
                self._client_cm.__aenter__(), self._loop
            )
            self._client = fut.result()

        def connect(self, host=None, port=None):
            # Connection already established via __aenter__ during
            # construction; kept as a no-op for API parity with paho-mqtt.
            return _Result(0)

        def publish(self, topic, payload=None, qos: int = 0):
            async def _do_publish():
                await self._client.publish(topic, payload=payload, qos=qos)

            fut = asyncio.run_coroutine_threadsafe(_do_publish(), self._loop)
            try:
                fut.result()
                return _Result(0)
            except Exception:
                return _Result(1)

    return _SyncClientFacade(host, port)


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
