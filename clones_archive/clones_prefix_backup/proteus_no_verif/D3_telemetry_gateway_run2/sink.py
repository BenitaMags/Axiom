"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import json


def _aiomqtt_sync_bridge_client(host: str = "localhost", port: int = 1883):
    """Bridge aiomqtt's async ``Client`` to the synchronous API paho-mqtt exposed.

    paho-mqtt's ``Client()`` constructor returns a usable object immediately;
    ``.connect(host, port)`` is a plain synchronous call, and ``.publish()``
    returns a ``MQTTMessageInfo`` object with an ``.rc`` attribute indicating
    success/failure -- none of it involves an event loop.

    aiomqtt's ``Client`` is instead an async context manager: it is
    constructed with the broker host/port, the actual network connection is
    only made when it is entered via ``async with`` (i.e. ``__aenter__``),
    and its ``.publish()`` is a coroutine that must be awaited. There is no
    synchronous constructor+connect step and no ``.rc`` return value.

    This wrapper creates a dedicated event loop, constructs the
    ``aiomqtt.Client``, and drives ``__aenter__`` synchronously via
    ``run_until_complete`` to establish the connection right away (mirroring
    paho's eager ``.connect()``). It returns a small synchronous shim object
    whose ``.publish(topic, payload)`` method runs the aiomqtt publish
    coroutine to completion on that same loop and fabricates a tiny object
    with ``.rc = 0`` (paho's "success" code) once it returns without raising,
    so existing code that reads ``info.rc`` keeps working unmodified.

    This is a best-effort shim, not a faithful reproduction of paho's
    connection lifecycle: it does not implement reconnects/keepalive, and
    unlike paho (which reports failures via nonzero ``rc``), aiomqtt raises
    exceptions on failure -- those exceptions are simply left to propagate
    here instead of being translated into an ``rc`` value.
    """
    import asyncio
    import aiomqtt  # aiomqtt's own API, used directly by this adapter

    loop = asyncio.new_event_loop()
    aio_client = aiomqtt.Client(host, port)
    loop.run_until_complete(aio_client.__aenter__())

    class _SyncBridge:
        def publish(self, topic, payload):
            loop.run_until_complete(aio_client.publish(topic, payload))

            class _Info:
                rc = 0

            return _Info()

        def __del__(self):
            try:
                loop.run_until_complete(
                    aio_client.__aexit__(None, None, None)
                )
            except Exception:
                pass
            loop.close()

    return _SyncBridge()


def _default_client(host: str = "localhost", port: int = 1883):
    import paho.mqtt.client as mqtt  # lazy import for testability

    client = _aiomqtt_sync_bridge_client(host, port)
    return client


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
