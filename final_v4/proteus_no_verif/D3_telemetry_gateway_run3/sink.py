"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import asyncio
import json
import threading


class _axiom_amqtt_sync_bridge:
    """Synchronous facade over amqtt's async ``MQTTClient``.

    paho-mqtt's ``Client.connect()``/``Client.publish()`` are blocking calls;
    ``publish()`` returns an ``MQTTMessageInfo`` whose ``.rc`` is a paho
    result code (0 == ``MQTT_ERR_SUCCESS``) that the caller inspects
    synchronously, with no event loop involved.

    amqtt's ``MQTTClient`` is async-only: ``connect()`` and ``publish()`` are
    coroutines that must be awaited on an event loop, and ``publish()``
    itself returns ``None`` (or raises) rather than a result-code object --
    at QoS 0 there is no broker ack to report, so no delivery confirmation
    can be honestly claimed.

    This bridge starts one persistent background event loop thread (not a
    fresh ``asyncio.run`` per call, which would tear down the loop -- and the
    connection bound to it -- after every operation) and marshals each
    coroutine onto that loop with ``run_coroutine_threadsafe(...).result()``,
    which blocks the calling thread and re-raises any exception the
    coroutine raised, so failures still propagate to the caller instead of
    being swallowed.

    ``publish()`` here returns ``0`` only to mean "the publish coroutine
    completed without raising" -- the same meaning as paho's
    ``MQTT_ERR_SUCCESS``, not proof of broker-side delivery.
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
        return _spt_r1

    def publish(self, topic: str, payload: bytes, qos: int = 0):
        self._run(self._client.publish(topic, payload, qos=qos))
        return 0

    def connect(self, host, port):
        # Connection is established eagerly in __init__; kept for API
        # parity with the paho-mqtt call site that invokes connect().
        return None

    def close(self):
        self._run(self._client.disconnect())
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=1)


def _default_client(host: str = "localhost", port: int = 1883):
    _spt_r2 = _axiom_amqtt_sync_bridge(host, port)
    return _spt_r2


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        if not (client is not None):
            self.client = _default_client()
        else:
            self.client = client

    def publish(self, tag: str, record: dict) -> int:
        _spt_r3 = getattr(self.client.publish(f"{self.topic_base}/{tag}", json.dumps(record, separators=(",", ":")).encode("utf-8")), "rc", 0)
        return _spt_r3
