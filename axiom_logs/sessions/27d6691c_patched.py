"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import asyncio
import json
import threading

from amqtt.client import MQTTClient


class _PublishResult:
    """Mimics paho-mqtt's MQTTMessageInfo enough for `getattr(info, "rc", 0)`.

    paho-mqtt's `publish()` returns an MQTTMessageInfo object whose `.rc`
    attribute is the paho result code (0 == MQTT_ERR_SUCCESS). amqtt's
    `MQTTClient.publish()` coroutine does not return any status object at
    all -- for qos 0 it is fire-and-forget, and even for qos >= 1 it just
    resolves once the ack completes, without exposing a paho-style code.
    We only ever construct this with rc=0, and only after the coroutine
    has completed without raising, so it means "publish call did not
    raise", not "broker guaranteed a qos-0 delivery" (qos 0 never has an
    ack to begin with, matching paho's own semantics there).
    """

    def __init__(self, rc: int):
        self.rc = rc


class _AmqttSyncClient:
    """Synchronous facade over amqtt's async-only `MQTTClient`.

    paho-mqtt's `Client` is instantiated once, connects synchronously via
    a blocking `.connect(host, port)`, and is then reused for repeated
    blocking `.publish(...)` calls across the lifetime of the process.

    amqtt's `MQTTClient` only exposes coroutines (`connect`, `publish`,
    `disconnect`) and must be driven by an asyncio event loop. To preserve
    the "connect once, publish many times over one live connection"
    behavior (rather than opening a new connection per publish, or using
    `asyncio.run` per call which tears down the loop -- and with it any
    transport state -- after every single call), this facade starts one
    background event loop thread that lives for the lifetime of the
    facade, and schedules every amqtt coroutine onto that same loop via
    `asyncio.run_coroutine_threadsafe(...).result()`. `.result()` blocks
    the calling thread until the coroutine finishes and re-raises any
    exception the coroutine raised, so amqtt errors propagate to callers
    exactly as paho-mqtt's synchronous calls would raise on failure.
    """

    def __init__(self, host: str = "localhost", port: int = 1883):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._client = MQTTClient()
        uri = f"mqtt://{host}:{port}/"
        self._run(self._client.connect(uri))

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def publish(self, topic: str, payload: bytes, qos: int = 0):
        self._run(self._client.publish(topic, payload, qos=qos))
        return _PublishResult(0)

    def close(self):
        self._run(self._client.disconnect())
        self._loop.call_soon_threadsafe(self._loop.stop)


def _amqtt_client_wrapper(host: str = "localhost", port: int = 1883) -> _AmqttSyncClient:
    """Build a synchronous, persistently-connected amqtt client.

    Replaces the old `paho.mqtt.client.Client(); client.connect(host, port)`
    pair. paho-mqtt gives back a `Client` object that is already connected
    and can be called synchronously and repeatedly. amqtt only offers an
    async `MQTTClient`, so this returns a `_AmqttSyncClient`, which owns a
    persistent background event loop and keeps the amqtt connection open
    across calls, exposing the same synchronous `.publish(topic, payload)`
    -> object-with-`.rc` shape that callers already expect.
    """
    return _AmqttSyncClient(host, port)


def _default_client(host: str = "localhost", port: int = 1883):
    return _amqtt_client_wrapper(host, port)


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
