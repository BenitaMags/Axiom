"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import json
import asyncio
import threading


class _AmqttSyncFacade:
    """Bridges amqtt's async ``MQTTClient`` to the synchronous API that
    ``paho.mqtt.client.Client`` used to provide.

    paho's ``Client()`` constructor plus ``.connect(host, port)`` gave back an
    object whose ``.publish(topic, payload)`` call was synchronous and
    returned an ``MQTTMessageInfo`` with an ``.rc`` status code (0 on
    success, raising ``ValueError`` for programmer errors like a wildcard
    topic).

    amqtt only exposes an async client: you must ``await client.connect(uri)``
    and ``await client.publish(topic, payload, qos)`` inside a coroutine.
    There is no synchronous equivalent.

    This facade starts one persistent background event loop (kept alive for
    the lifetime of the facade, not a throwaway ``asyncio.run`` per call, so
    the connection the client opens stays bound to a live loop). Each public
    method schedules its coroutine onto that loop with
    ``run_coroutine_threadsafe`` and blocks for the result, re-raising
    whatever exception amqtt raised so failures are never swallowed.

    ``publish`` awaits amqtt's ``publish()`` coroutine. For QoS 0 that only
    proves the message was handed to the transport (no delivery
    acknowledgement exists at that QoS), so we return 0 to mean "publish call
    completed without raising" -- mirroring paho's ``rc == 0`` "no error"
    convention -- rather than claiming delivery was confirmed.
    """

    def __init__(self, host: str = "localhost", port: int = 1883):
        from amqtt.client import MQTTClient  # lazy import for testability

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._client = MQTTClient()
        self._run(self._client.connect(f"mqtt://{host}:{port}/"))

    def _run(self, coro):
        _spt_r1 = asyncio.run_coroutine_threadsafe(coro, self._loop).result()
        # Blocks the calling (synchronous) thread until the coroutine
        # finishes on the persistent loop, and re-raises any exception it
        # raised so callers see failures the way they did with paho.
        return _spt_r1

    def publish(self, topic: str, payload: bytes, qos: int = 0):
        self._run(self._client.publish(topic, payload, qos=qos))
        return 0

    def close(self):
        self._run(self._client.disconnect())
        self._loop.call_soon_threadsafe(self._loop.stop)


def _amqtt_client_wrapper(host: str = "localhost", port: int = 1883):
    """Construct and connect an amqtt-backed client that mimics the
    synchronous object ``paho.mqtt.client.Client()`` + ``.connect(host,
    port)`` used to return, so existing synchronous call sites (like
    ``MqttSink.publish``) keep working unchanged.

    amqtt has no synchronous constructor/connect; both are coroutines. This
    wrapper hides that behind ``_AmqttSyncFacade``, which owns a persistent
    background event loop so the connection stays valid across calls (unlike
    a fresh ``asyncio.run`` per call, which would tear the loop -- and the
    connection bound to it -- down immediately).
    """
    _spt_r2 = _AmqttSyncFacade(host, port)
    return _spt_r2


def _default_client(host: str = "localhost", port: int = 1883):
    _spt_r3 = _amqtt_client_wrapper(host, port)
    return _spt_r3


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        if not (client is not None):
            self.client = _default_client()
        else:
            self.client = client

    def publish(self, tag: str, record: dict) -> int:
        _spt_r4 = getattr(self.client.publish(f"{self.topic_base}/{tag}", json.dumps(record, separators=(",", ":")).encode("utf-8")), "rc", 0)
        return _spt_r4
