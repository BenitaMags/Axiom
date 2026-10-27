"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for

import asyncio
import threading


class _PublishResult:
    """Stand-in for paho-mqtt's MQTTMessageInfo.

    paho-mqtt's ``Client.publish()`` returns an ``MQTTMessageInfo`` whose
    ``.rc`` attribute holds a paho result code (0 == MQTT_ERR_SUCCESS).
    amqtt's ``MQTTClient.publish()`` coroutine does not return any status
    object -- if it returns without raising, the publish was handed off
    successfully. We therefore only ever construct this with rc=0, and only
    after the awaited publish() call has completed without raising. Any
    failure surfaces as a real exception from amqtt, exactly as paho-mqtt
    would have surfaced a raised exception (e.g. for a bad topic) rather
    than a nonzero rc.
    """

    def __init__(self, rc: int = 0):
        self.rc = rc


class _AmqttSyncClient:
    """Synchronous-looking facade over amqtt's async MQTTClient.

    amqtt only exposes async connect()/publish()/disconnect() coroutines,
    but TelemetryBridge (and its callers) are written synchronously. This
    facade owns a persistent background event loop (started once, kept
    alive for the lifetime of the facade) and marshals each call onto that
    loop with ``run_coroutine_threadsafe(...).result()``, which blocks the
    calling thread until the coroutine finishes and re-raises any exception
    the coroutine raised. Using a persistent loop (rather than asyncio.run
    per call) is required because amqtt binds its network transport to the
    loop that was active when connect() was called; tearing the loop down
    between calls would leave publish() operating on dead transport state.
    """

    def __init__(self, client_id=None):
        from amqtt.client import MQTTClient  # lazy import for testability

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._client = MQTTClient(client_id=client_id)

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def connect(self, host, port):
        uri = f"mqtt://{host}:{port}/"
        return self._run(self._client.connect(uri))

    def publish(self, topic, payload, qos=0):
        """Publish a message and report a paho-style result.

        amqtt's publish() coroutine returns nothing on success (it only
        confirms the message was queued/sent, not that qos>=1 delivery was
        acknowledged beyond what amqtt itself awaits internally). If the
        coroutine raises, that exception propagates to the caller unchanged
        -- we do not convert it into a return code, matching paho-mqtt's
        behavior of raising (e.g. ValueError) on bad input rather than
        returning a failure rc for such cases. On successful completion we
        report rc=0, mirroring paho-mqtt's MQTT_ERR_SUCCESS.
        """
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        self._run(self._client.publish(topic, payload, qos=qos))
        return _PublishResult(0)

    def disconnect(self):
        self._run(self._client.disconnect())
        self._loop.call_soon_threadsafe(self._loop.stop)


def _amqtt_sync_client_wrapper(client_id=None):
    """Factory returning a synchronous-facing amqtt client proxy.

    Bridges the API gap between paho-mqtt's synchronous Client and amqtt's
    async-only MQTTClient by returning an `_AmqttSyncClient` instance that
    exposes `.connect(host, port)` and `.publish(topic, payload, qos=...)`
    as ordinary blocking methods, running the real work on a persistent
    background event loop so connection state remains valid across calls.
    """
    return _AmqttSyncClient(client_id=client_id)


def _default_client(cfg: BridgeConfig):
    client = _amqtt_sync_client_wrapper(client_id=cfg.client_id)
    client.connect(cfg.host, cfg.port)
    return client


class TelemetryBridge:
    def __init__(self, cfg: BridgeConfig, client=None):
        self.cfg = cfg
        self.client = client if client is not None else _default_client(cfg)

    def publish_reading(self, tag: str, reading: dict) -> dict:
        topic = topic_for(self.cfg.base_topic, tag)
        payload = encode(reading)
        info = self.client.publish(topic, payload, qos=self.cfg.qos)
        return {
            "topic": topic,
            "payload": payload,
            "qos": self.cfg.qos,
            "rc": getattr(info, "rc", 0),
        }

    def publish_batch(self, readings: dict) -> list:
        """Publish {tag: reading} pairs, returning the per-message results."""
        return [self.publish_reading(tag, r) for tag, r in readings.items()]
