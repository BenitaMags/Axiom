"""Synchronous glue around amqtt's asyncio-only MQTTClient.

Split out of bridge.py: this module owns the thread/event-loop plumbing that
makes amqtt's coroutine-based client usable from synchronous call sites, and
the small result type that mimics paho-mqtt's `MQTTMessageInfo.rc` surface.
"""
from mqtt_config import BridgeConfig

import asyncio
import threading


class _AmqttSyncResult:
    """Stand-in for paho-mqtt's `MQTTMessageInfo`.

    paho-mqtt's `Client.publish()` returns an `MQTTMessageInfo` whose `.rc`
    attribute is `0` when the message was queued successfully and a nonzero
    `MQTT_ERR_*` code otherwise (e.g. `MQTT_ERR_NO_CONN` when not connected).
    amqtt's `MQTTClient.publish()` is a coroutine that returns `None` on
    success and raises an exception instead of encoding a status. This class
    just carries the `rc` value the facade below derived from that
    exception (or lack of one), so `publish_reading()`'s
    `getattr(info, "rc", 0)` keeps working unchanged.
    """

    def __init__(self, rc: int):
        self.rc = rc


class _axiom_amqtt_sync_client_wrapper:
    """Synchronous facade around amqtt's asyncio-only `MQTTClient`.

    paho-mqtt's `Client` is used here synchronously: plain, blocking
    `connect()` and `publish()` calls. amqtt's `MQTTClient` only exposes
    coroutines (`connect()`, `publish()`, `disconnect()`) that must run on an
    asyncio event loop. To preserve the synchronous call sites in
    `TelemetryBridge` without rewriting them into async code, this facade
    starts one persistent background event loop thread when constructed and
    marshals every amqtt coroutine onto that *same* loop via
    `asyncio.run_coroutine_threadsafe(...).result()`. A fresh `asyncio.run()`
    per call was deliberately avoided: it tears its loop down when the
    coroutine returns, so the connection amqtt opened becomes bound to a
    dead loop and the next call fails against closed transport state.

    `publish()` synthesizes an `.rc`-bearing result object because
    `publish_reading()` reads `getattr(info, "rc", 0)`. paho-mqtt itself
    returns `rc != 0` (rather than raising) specifically for connection/
    transport-level publish failures, so the same class of amqtt failures
    (connection lost, not connected, transport errors) is translated to
    `rc=1` here; anything else propagates unchanged as amqtt raises it, just
    as paho-mqtt's own `ValueError`s (e.g. bad topic) were never converted
    to an `rc` and always reached the caller directly.
    """

    def __init__(self, cfg: BridgeConfig):
        import amqtt.client as _amqtt_client  # lazy import for testability

        self._client_module = _amqtt_client
        self._transport_errors = (
            _amqtt_client.ClientException,
            ConnectionError,
            asyncio.TimeoutError,
            OSError,
        )
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._client = _amqtt_client.MQTTClient(client_id=cfg.client_id)
        self._cfg = cfg

    def _run(self, coro):
        """Run `coro` to completion on the persistent background loop.

        Blocks the calling (synchronous) thread until the coroutine
        finishes, and re-raises whatever exception it raised so failures
        reach the caller instead of being lost across the thread boundary.
        """
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def connect(self, host, port):
        uri = f"mqtt://{host}:{port}/"
        self._run(self._client.connect(uri))

    def publish(self, topic, payload, qos=0):
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        try:
            self._run(self._client.publish(topic, payload, qos=qos))
        except self._transport_errors:
            return _AmqttSyncResult(1)
        return _AmqttSyncResult(0)

    def close(self):
        self._run(self._client.disconnect())
        self._loop.call_soon_threadsafe(self._loop.stop)
