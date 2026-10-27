"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _AxiomSyncMQTTClient:
    """Synchronous-looking facade over amqtt's async MQTTClient.

    paho-mqtt's ``Client`` is a synchronous object: ``connect()`` blocks (or is
    driven by an internal network loop) and ``publish()`` returns a
    ``MQTTMessageInfo`` object exposing an ``.rc`` return-code attribute.

    amqtt's ``MQTTClient`` (amqtt.client.MQTTClient) is asyncio-native:
    ``connect()`` and ``publish()`` are coroutines that must be awaited, and
    on success ``publish()`` simply returns ``None`` (or raises on failure)
    rather than yielding a return-code object.

    This wrapper owns a private, persistent asyncio event loop and uses it to
    drive the coroutines synchronously from ordinary (non-async) calling code:
    the constructor awaits ``connect()`` immediately, and ``publish()`` here
    awaits amqtt's coroutine and translates the outcome (``None`` on success,
    or a swallowed exception recorded as a nonzero code) into a tiny result
    object exposing ``.rc``, mirroring paho-mqtt's return contract so callers
    like ``publish_reading`` do not need to become async or change shape.
    """

    class _Result:
        def __init__(self, rc):
            self.rc = rc

    def __init__(self, cfg: BridgeConfig):
        import asyncio
        from amqtt.client import MQTTClient

        self._asyncio = asyncio
        self._loop = asyncio.new_event_loop()
        self._client = MQTTClient(client_id=cfg.client_id, loop=self._loop)
        uri = "mqtt://{}:{}".format(cfg.host, cfg.port)
        self._loop.run_until_complete(self._client.connect(uri))

    def publish(self, topic, payload, qos=0):
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        try:
            self._loop.run_until_complete(
                self._client.publish(topic, payload, qos=qos)
            )
            return _AxiomSyncMQTTClient._Result(0)
        except Exception:
            return _AxiomSyncMQTTClient._Result(1)

    def disconnect(self):
        self._loop.run_until_complete(self._client.disconnect())


def _axiom_mqtt_client_wrapper(cfg: BridgeConfig):
    """Build a synchronous-style MQTT client backed by amqtt.

    Replaces the paho-mqtt ``mqtt.Client(client_id=...)`` + ``connect(host,
    port)`` pattern with amqtt's async ``MQTTClient``, bridged to a
    synchronous call interface via ``_AxiomSyncMQTTClient`` so the rest of
    this module (which is written synchronously) does not need to change.
    """
    return _AxiomSyncMQTTClient(cfg)


def _default_client(cfg: BridgeConfig):
    client = _axiom_mqtt_client_wrapper(cfg)
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
