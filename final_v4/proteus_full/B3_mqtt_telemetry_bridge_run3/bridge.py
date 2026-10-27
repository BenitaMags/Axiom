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
    """Shim carrying an ``rc`` attribute so callers using
    ``getattr(info, 'rc', 0)`` (the paho-mqtt ``MQTTMessageInfo.rc``
    convention) keep working unchanged. amqtt's ``publish()`` coroutine is
    fire-and-forget at qos 0 and returns ``None`` on success (raising on
    failure), so ``rc=0`` here only means "the publish coroutine completed
    without raising / was queued", not "the broker acknowledged receipt".
    """

    def __init__(self, rc: int = 0):
        self.rc = rc


class _amqtt_sync_client_wrapper:
    """Synchronous facade over ``amqtt.client.MQTTClient``.

    paho-mqtt's ``mqtt.Client`` is synchronous: ``.connect()`` blocks until
    the handshake completes, and ``.publish()`` returns an
    ``MQTTMessageInfo`` object with an ``.rc`` result code immediately.

    amqtt's ``MQTTClient`` is async-only: ``.connect()`` and ``.publish()``
    are coroutines that must run on an asyncio event loop. To present the
    same synchronous ``connect()``/``publish()`` surface that
    ``TelemetryBridge`` and ``_default_client`` expect -- and to avoid the
    bug where each call does its own ``asyncio.run()`` and tears down the
    connection's loop in between -- this wrapper starts ONE persistent
    background event loop in a daemon thread (created once, in
    ``__init__``) and marshals every subsequent call onto that same loop via
    ``asyncio.run_coroutine_threadsafe(...).result()``. That blocks the
    calling thread until the coroutine finishes and re-raises whatever
    exception it raised, so amqtt errors reach the caller exactly as
    paho-mqtt's would.

    ``publish()`` returns a ``_PublishResult`` shim carrying ``.rc`` (always
    0 when the underlying coroutine did not raise) purely for API
    compatibility with ``publish_reading``'s ``getattr(info, "rc", 0)``.
    """

    def __init__(self, client_id: str):
        from amqtt.client import MQTTClient

        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()
        self._client = MQTTClient(client_id=client_id)

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def connect(self, host: str, port: int):
        uri = f"mqtt://{host}:{port}/"
        return self._run(self._client.connect(uri))

    def publish(self, topic: str, payload, qos: int = 0):
        self._run(self._client.publish(topic, payload, qos=qos))
        return _PublishResult(0)

    def disconnect(self):
        return self._run(self._client.disconnect())


def _default_client(cfg: BridgeConfig):

    client = _amqtt_sync_client_wrapper(cfg.client_id)
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
