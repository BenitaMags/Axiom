"""Glue layer bridging amqtt's async `MQTTClient` to a synchronous call contract.

Kept separate from `bridge.py` so the asyncio/threading plumbing needed to make
amqtt's coroutine-based API behave like paho-mqtt's synchronous API is isolated
from the telemetry-publishing logic that depends on it.
"""
from mqtt_config import BridgeConfig

import asyncio
import threading


class _AxiomPublishResult:
    """Mimics paho-mqtt's MQTTMessageInfo enough for call sites that read `.rc`.

    paho-mqtt's `Client.publish()` returns an `MQTTMessageInfo` object whose
    `.rc` attribute is `MQTT_ERR_SUCCESS` (0) when the message was successfully
    handed off to the outgoing queue, or a nonzero error code otherwise (no
    exception is raised for those non-fatal codes). amqtt's `MQTTClient.publish()`
    is a coroutine that returns `None` on success and raises an exception on
    failure instead of returning an error code. Since we let amqtt's exceptions
    propagate unchanged (see `_axiom_mqtt_async_bridge_wrapper.publish`), by the
    time this object is constructed the publish has already succeeded, so `.rc`
    is always 0 here -- it exists only so call sites that read
    `getattr(info, "rc", 0)` keep working unchanged.
    """

    rc = 0


class _axiom_mqtt_async_bridge_wrapper:
    """Synchronous facade over amqtt's async `MQTTClient`.

    paho-mqtt's `Client` is a plain synchronous object: `connect()` blocks (or
    starts a background network loop) and `publish()` returns immediately with
    a status object. amqtt's `MQTTClient` is async-only: connecting and
    publishing are both coroutines that must run on an asyncio event loop.

    To preserve the synchronous call contract used by `_default_client`,
    `TelemetryBridge.publish_reading`, and `TelemetryBridge.publish_batch`
    (none of which are coroutines, and none of which know about asyncio),
    this wrapper starts one persistent background event loop thread and runs
    every amqtt coroutine on it via
    `asyncio.run_coroutine_threadsafe(...).result()`. Using `asyncio.run()`
    per call was rejected because each call would tear down the event loop --
    and with it the live connection/transport -- so a connect() on one loop
    followed by a publish() on a different, already-dead loop breaks silently.
    A single long-lived loop keeps the connection valid across calls, matching
    paho-mqtt's persistent-connection semantics.
    """

    def __init__(self, cfg: BridgeConfig):
        from amqtt.client import MQTTClient  # lazy import for testability

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._client = MQTTClient(client_id=cfg.client_id)
        uri = f"mqtt://{cfg.host}:{cfg.port}"
        self._run(self._client.connect(uri))

    def _run(self, coro):
        """Run `coro` on the persistent background loop and block for its result.

        Blocks the calling (synchronous) thread until the coroutine completes
        on the persistent loop, and re-raises whatever exception it raised, so
        failures continue to reach callers exactly as they would with
        paho-mqtt's synchronous calls.
        """
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def publish(self, topic, payload, qos=0):
        """Publish and return a `.rc`-bearing object like paho-mqtt's `publish()`.

        amqtt's `MQTTClient.publish()` is a coroutine that returns `None` on
        success and raises (e.g. `amqtt.errors.ClientError`, `ConnectError`,
        or `MQTTError` for protocol/transport failures) rather than returning
        an error code. We let those exceptions propagate unchanged -- we do
        NOT catch-and-swallow them -- since paho-mqtt itself let exceptions
        (e.g. ValueError for bad topics) reach the caller too. We only
        translate the *successful* case into an object with `.rc == 0` so
        that `getattr(info, "rc", 0)` at the call site keeps working. Note
        that awaiting this only proves the publish was sent/queued by the
        client, not that a broker acked delivery for qos 0.
        """
        self._run(self._client.publish(topic, payload, qos=qos))
        return _AxiomPublishResult()

    def disconnect(self):
        """Best-effort symmetric teardown; not required by any call site yet."""
        self._run(self._client.disconnect())
        self._loop.call_soon_threadsafe(self._loop.stop)
