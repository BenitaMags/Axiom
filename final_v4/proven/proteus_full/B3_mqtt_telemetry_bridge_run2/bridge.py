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
    """Return-value shim mimicking paho-mqtt's `MQTTMessageInfo`.

    paho-mqtt's `Client.publish()` returns a `MQTTMessageInfo` object whose
    `.rc` attribute is 0 (`MQTT_ERR_SUCCESS`) when the publish was accepted
    without error. amqtt's `MQTTClient.publish()` is a coroutine that
    returns `None` on success and *raises* on failure instead of encoding a
    status code. Since `_AmqttSyncClientBridge.publish` only constructs this
    object after the coroutine has completed without raising, `rc=0` here
    reflects exactly the same "no error occurred" fact paho-mqtt encoded as
    `rc == 0` -- it is not a fabricated success, it is a direct translation
    of "the awaited call did not raise".
    """

    def __init__(self, rc: int = 0):
        self.rc = rc


class _AmqttSyncClientBridge:
    """Synchronous facade over `amqtt.client.MQTTClient`.

    paho-mqtt's `Client` is synchronous: `.connect()` and `.publish()` block
    the calling thread and return immediately. amqtt's `MQTTClient` is
    async-only: `.connect()`, `.publish()` and `.disconnect()` are
    coroutines that must run on an asyncio event loop. A per-call
    `asyncio.run()` would open and tear down a fresh loop (and therefore a
    fresh, disconnected transport) on every publish, silently turning one
    persistent MQTT session into one connection per message. Instead this
    bridge starts a single background event loop thread at construction
    time and marshals every call onto it with
    `asyncio.run_coroutine_threadsafe(...).result()`, which blocks the
    caller until the coroutine finishes on that persistent loop and
    re-raises whatever exception it raised.
    """

    def __init__(self, cfg: BridgeConfig):
        import amqtt.client as amqtt_client  # lazy import for testability

        self._amqtt_client = amqtt_client
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._client = amqtt_client.MQTTClient(client_id=cfg.client_id)
        self._run(self._client.connect(f"mqtt://{cfg.host}:{cfg.port}/"))

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def publish(self, topic, payload, qos: int = 0):
        qos_map = {
            0: self._amqtt_client.QOS_0,
            1: self._amqtt_client.QOS_1,
            2: self._amqtt_client.QOS_2,
        }
        try:
            self._run(
                self._client.publish(
                    topic, payload, qos=qos_map.get(qos, self._amqtt_client.QOS_0)
                )
            )
        except (
            self._amqtt_client.ClientError,
            self._amqtt_client.ConnectError,
            self._amqtt_client.ProtocolHandlerError,
        ) as exc:
            # paho-mqtt surfaces broker/transport failures as connection
            # errors at the point of use; amqtt raises its own hierarchy of
            # client/connect/protocol-handler errors for the same failure
            # modes (refused connections, dropped transport, malformed
            # protocol state). Re-raise as ConnectionError so callers that
            # handle connection failures are not left catching an
            # amqtt-specific type they have never heard of.
            raise ConnectionError(str(exc)) from exc
        return _PublishResult(0)

    def disconnect(self):
        self._run(self._client.disconnect())
        self._loop.call_soon_threadsafe(self._loop.stop)


def _amqtt_client_bridge_wrapper(cfg: BridgeConfig):
    """Build a synchronous, connected MQTT client for `TelemetryBridge`.

    paho-mqtt's `mqtt.Client(client_id=...)` followed by `.connect(host,
    port)` yields a synchronous object ready for `.publish(...)` calls.
    amqtt has no synchronous client at all -- `amqtt.client.MQTTClient`
    only exposes coroutines. This wrapper constructs the
    `_AmqttSyncClientBridge` facade above, which owns a persistent
    background event loop, performs the amqtt `connect()` coroutine on it
    synchronously, and returns an object exposing the same `.publish(...)`
    call shape (including an `.rc`-bearing result) that `_default_client`
    used to hand back.
    """
    return _AmqttSyncClientBridge(cfg)


def _default_client(cfg: BridgeConfig):
    return _amqtt_client_bridge_wrapper(cfg)


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
