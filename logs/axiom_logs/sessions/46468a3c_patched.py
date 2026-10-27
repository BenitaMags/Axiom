"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for

import asyncio
import threading


class _GmqttPublishResult:
    """Stand-in for paho-mqtt's `MQTTMessageInfo` so `getattr(info, "rc", 0)` keeps working.

    paho-mqtt's `Client.publish()` returns an `MQTTMessageInfo` object whose
    `.rc` attribute is an `MQTT_ERR_*` status code, set synchronously the
    moment the message is queued for sending (it does NOT mean the broker
    acknowledged delivery). gmqtt's `Client.publish()` does not return
    anything usable for this: for QoS 0 it returns `None`, and for QoS >= 1
    the PUBACK arrives later via an `on_publish` callback rather than as a
    return value. We therefore cannot fabricate a delivery-confirmed status;
    we only report that the client handed the message off, which mirrors
    what paho's rc==0 actually promises. Any failure raised while handing
    the message off (e.g. not connected) is left to propagate, not
    swallowed here.
    """

    def __init__(self, rc: int = 0):
        self.rc = rc


class _gmqtt_sync_client_wrapper:
    """Bridges gmqtt's async `Client` to the synchronous connect()/publish() calls this file expects.

    paho-mqtt's `Client.connect()` blocks until the handshake finishes and
    `Client.publish()` sends immediately, both as ordinary synchronous
    calls. gmqtt's `Client.connect()` is a coroutine that must run on an
    asyncio event loop, and `Client.publish()` may need that same
    connection/loop to already be alive.

    Rather than `asyncio.run()` per call (which would tear the loop -- and
    the connection bound to it -- down immediately, breaking the next
    call), this wrapper starts a single persistent background event loop
    for the lifetime of the client and marshals every call onto it with
    `run_coroutine_threadsafe(...).result()`. `.result()` blocks the
    calling thread until the coroutine finishes and re-raises whatever
    exception it raised, so callers see failures exactly as they would
    from a synchronous client instead of having them silently dropped.
    """

    def __init__(self, client_id: str):
        import gmqtt  # lazy import for testability, mirrors prior lazy import style

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._client = gmqtt.Client(client_id)

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def connect(self, host, port):
        return self._run(self._client.connect(host, port))

    def publish(self, topic, payload, qos: int = 0):
        # gmqtt's real `Client.publish()` has no `return <value>` statement
        # (it queues the message onto the connection and gives back nothing
        # usable) -- it is fire-and-forget for QoS 0 and acks arrive later
        # via an `on_publish` callback for QoS >= 1. We deliberately do NOT
        # capture its result in a variable: doing so trips pylint's
        # assignment-from-no-return check, and there is nothing useful to
        # capture anyway. Any exception raised while handing the message
        # off (e.g. not connected) propagates to the caller unmodified.
        self._client.publish(topic, payload, qos=qos)
        return _GmqttPublishResult(0)


def _default_client(cfg: BridgeConfig):

    client = _gmqtt_sync_client_wrapper(cfg.client_id)
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
