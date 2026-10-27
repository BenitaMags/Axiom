"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import asyncio
import json
import threading

import aiomqtt


class _AxiomPublishResult:
    """Mimics paho-mqtt's `MQTTMessageInfo` enough for `.rc` access.

    paho-mqtt's `Client.publish()` returns an `MQTTMessageInfo` object whose
    `.rc` attribute is `mqtt.MQTT_ERR_SUCCESS` (0) when the message was
    successfully queued, or a nonzero paho error code otherwise. aiomqtt's
    `Client.publish()` coroutine returns nothing at all -- it either
    completes (meaning the PUBLISH packet was sent) or raises. We wrap a
    successful (non-raising) publish in an object exposing `.rc = 0` so
    existing callers that read `.rc` keep working. We never invent a
    nonzero `.rc`: any failure is left to propagate as a real exception,
    never swallowed into a status code.
    """

    def __init__(self, rc: int):
        self.rc = rc


class _axiom_mqtt_client_wrapper:
    """Synchronous facade bridging aiomqtt's async `Client` to a paho-like API.

    paho-mqtt's `Client` connects synchronously (`client.connect(host, port)`)
    and publishes synchronously (`client.publish(topic, payload)`, returning
    an object with `.rc`). aiomqtt's `Client` is only usable as an
    `async with` context manager, and its `publish()` is a coroutine that
    must be awaited on a running event loop.

    To keep `MqttSink.publish` synchronous -- so its callers don't have to
    become async -- this wrapper starts a single persistent background event
    loop in a daemon thread, opens the aiomqtt connection on that loop once
    (via `__aenter__`, the async equivalent of paho's `connect()`), and then
    marshals each subsequent call onto that same loop with
    `asyncio.run_coroutine_threadsafe(...).result()`. Reusing one long-lived
    loop (instead of `asyncio.run()` per call) is required: `asyncio.run()`
    tears its loop down when the coroutine finishes, which would destroy the
    connection's transport and break every call after the first.

    `._run()`'s `.result()` blocks until the coroutine finishes and
    re-raises whatever aiomqtt raised, so callers observe failures exactly
    as they would have from a raising paho-mqtt call -- nothing here catches
    or swallows exceptions.
    """

    def __init__(self, host: str = "localhost", port: int = 1883):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._client = aiomqtt.Client(host, port)
        self._run(self._client.__aenter__())

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def publish(self, topic: str, payload: bytes):
        """Publish `payload` to `topic`, returning a paho-like `.rc` result.

        aiomqtt's `publish()` coroutine returns nothing on success; awaiting
        it only confirms the PUBLISH packet was handed off (at the default
        QoS 0 there is no broker acknowledgement to wait for, so this is not
        a delivery guarantee -- just parity with what paho-mqtt's return
        value represented). We report that as `.rc = 0`
        (`MQTT_ERR_SUCCESS`). If aiomqtt raises -- protocol errors and
        transport errors alike (e.g. `aiomqtt.MqttError` and connection
        failures) -- the exception propagates unchanged; we do not catch it
        here.
        """
        self._run(self._client.publish(topic, payload))
        return _AxiomPublishResult(0)

    def close(self):
        self._run(self._client.__aexit__(None, None, None))
        self._loop.call_soon_threadsafe(self._loop.stop)


def _default_client(host: str = "localhost", port: int = 1883):

    return _axiom_mqtt_client_wrapper(host, port)


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
