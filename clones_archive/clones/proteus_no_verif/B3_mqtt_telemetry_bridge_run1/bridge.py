"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


def _axiom_mqtt_client_wrapper(cfg: BridgeConfig, mqtt_module):
    """Adapt an async amqtt MQTTClient to the synchronous client interface
    that TelemetryBridge (and the rest of this module) was written against.

    paho-mqtt's ``Client.connect()``/``Client.publish()`` are synchronous,
    blocking calls, and ``publish()`` returns a ``MQTTMessageInfo`` object
    exposing an ``.rc`` result code that callers check via
    ``getattr(info, "rc", 0)``. amqtt's ``MQTTClient`` instead exposes
    coroutine methods (``connect``, ``publish``, ``disconnect``) that must
    be awaited, and its ``publish()`` returns ``None`` -- there is no
    ``.rc`` attribute, since amqtt handles QoS acknowledgement internally.

    This wrapper constructs an amqtt ``MQTTClient`` and returns a small
    adapter object whose ``connect``/``publish``/``disconnect`` methods
    look and behave synchronously: each one drives the corresponding amqtt
    coroutine to completion on a dedicated event loop via
    ``loop.run_until_complete``. ``publish`` also synthesizes a tiny result
    object carrying an ``.rc`` attribute (0 on success, 1 if the coroutine
    raised) so existing call sites that do
    ``getattr(info, "rc", 0)`` continue to work unmodified.
    """
    import asyncio

    loop = asyncio.new_event_loop()
    amqtt_client = mqtt_module.MQTTClient(client_id=cfg.client_id)

    class _PublishInfo:
        def __init__(self, rc=0):
            self.rc = rc

    class _SyncClientAdapter:
        def connect(self, host, port):
            uri = f"mqtt://{host}:{port}"
            return loop.run_until_complete(amqtt_client.connect(uri))

        def publish(self, topic, payload, qos=0):
            try:
                loop.run_until_complete(
                    amqtt_client.publish(topic, payload, qos=qos)
                )
                return _PublishInfo(rc=0)
            except Exception:
                return _PublishInfo(rc=1)

        def disconnect(self):
            return loop.run_until_complete(amqtt_client.disconnect())

    return _SyncClientAdapter()


def _default_client(cfg: BridgeConfig):
    import paho.mqtt.client as mqtt  # lazy import for testability

    client = _axiom_mqtt_client_wrapper(cfg, mqtt)
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
