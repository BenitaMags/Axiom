"""Support classes bridging amqtt's async client API to a synchronous facade.

Split out of bridge.py as a cohesive helper: the async-to-sync marshaling
machinery is a distinct concern from the telemetry publishing logic that
uses it.
"""
from mqtt_config import BridgeConfig

import asyncio
import threading


class _PublishResult:
    """Mimics paho-mqtt's MQTTMessageInfo enough for `getattr(info, "rc", 0)`.

    paho-mqtt's `client.publish()` returns an MQTTMessageInfo whose `.rc`
    attribute is 0 on success (MQTT_ERR_SUCCESS) and nonzero on failure.
    amqtt's `client.publish()` is a coroutine that returns nothing on success
    and raises an exception on failure -- there is no status code to read.
    We only ever construct this with rc=0, and only after the awaited
    publish coroutine has returned without raising; if it raised, the
    exception propagates to the caller instead of being converted into a
    fabricated rc, the same way paho-mqtt itself raises (e.g. ValueError for
    a bad topic) rather than returning a failure code for that case.
    """

    def __init__(self, rc: int = 0):
        self.rc = rc


class _EventLoopThread:
    """Owns a background event loop running forever on its own thread.

    Encapsulates the "persistent loop marshaled via
    run_coroutine_threadsafe" pattern so the amqtt facade can focus on the
    publish/connect API shape instead of loop plumbing.
    """

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)


class _amqtt_client_wrapper:
    """Synchronous facade bridging amqtt.client.MQTTClient to paho-mqtt's API.

    paho-mqtt's `mqtt.Client` is synchronous: `.connect(host, port)` blocks
    until connected and `.publish(topic, payload, qos=...)` returns an
    MQTTMessageInfo immediately. amqtt's `MQTTClient` is async-only:
    `await client.connect(uri)` / `await client.publish(topic, payload,
    qos=...)`. To keep the existing synchronous call sites in
    TelemetryBridge working, this facade starts one persistent background
    event loop (kept running for the facade's whole lifetime) and marshals
    each call onto it with `asyncio.run_coroutine_threadsafe(...).result()`,
    which blocks the calling thread until the coroutine completes and
    re-raises whatever exception it raised. Using `asyncio.run()` per call
    would tear the loop -- and the connection bound to it -- down after
    every call, so the next publish would run against dead transport state;
    the persistent loop avoids that.
    """

    def __init__(self, cfg: BridgeConfig):
        import amqtt.client as amqtt_client  # lazy import for testability

        self._runner = _EventLoopThread()
        self._client = amqtt_client.MQTTClient(client_id=cfg.client_id)
        uri = f"mqtt://{cfg.host}:{cfg.port}/"
        self._run(self._client.connect(uri))

    def _run(self, coro):
        return self._runner.run(coro)

    def publish(self, topic, payload, qos=0):
        """Publish via amqtt and return a paho-style result exposing `.rc`.

        amqtt's `publish()` coroutine returns None (or raises) instead of a
        status object. A completed await only confirms the message was
        handed off through amqtt's send path, so rc=0 here means "no
        exception was raised", matching the meaning of paho-mqtt's
        MQTT_ERR_SUCCESS. Any failure (protocol or transport) propagates as
        a raised exception rather than being swallowed into a return code,
        just as paho-mqtt itself raises for equivalent failures.
        """
        self._run(self._client.publish(topic, payload, qos=qos))
        return _PublishResult(0)

    def disconnect(self):
        self._run(self._client.disconnect())
        self._runner.stop()
