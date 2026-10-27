"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for

import asyncio


def _axiom_amqtt_client_wrapper(client_id: str, host: str, port: int):
    """Bridge paho-mqtt's synchronous Client API onto amqtt's async MQTTClient.

    paho-mqtt's ``mqtt.Client(client_id=...)`` returns an object exposing
    synchronous ``.connect(host, port)`` and ``.publish(topic, payload, qos)``
    methods; ``.publish()`` returns a ``MQTTMessageInfo`` whose ``.rc``
    attribute is the paho result code (0 == success).

    amqtt's ``amqtt.client.MQTTClient`` is asyncio-native: both ``connect()``
    and ``publish()`` are coroutines that must be awaited (typically inside an
    ``async with``-managed client), and there is no built-in synchronous
    facade equivalent to paho-mqtt's.

    This wrapper constructs an amqtt ``MQTTClient``, immediately drives its
    ``connect()`` coroutine to completion on a dedicated private event loop
    (so instantiation still "looks" synchronous to callers), and returns a
    small shim object whose ``.publish(topic, payload, qos)`` method runs the
    ``MQTTClient.publish()`` coroutine to completion on that same loop and
    returns an object exposing ``.rc`` (0 on success, 1 on failure), matching
    the shape existing call sites expect from `paho-mqtt`.
    """
    from amqtt.client import MQTTClient

    loop = asyncio.new_event_loop()
    amqtt_client = MQTTClient(client_id=client_id)
    loop.run_until_complete(amqtt_client.connect(f"mqtt://{host}:{port}/"))

    class _SyncPublishResult:
        def __init__(self, rc):
            self.rc = rc

    class _SyncClientShim:
        def publish(self, topic, payload, qos=0):
            try:
                loop.run_until_complete(
                    amqtt_client.publish(topic, payload, qos=qos)
                )
                return _SyncPublishResult(0)
            except Exception:
                return _SyncPublishResult(1)

        def connect(self, *args, **kwargs):
            # Connection is already established when the wrapper is
            # constructed; kept only so callers expecting a paho-mqtt-style
            # .connect() method don't break if they call it again.
            return None

    return _SyncClientShim()


def _default_client(cfg: BridgeConfig):

    return _axiom_amqtt_client_wrapper(cfg.client_id, cfg.host, cfg.port)


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
