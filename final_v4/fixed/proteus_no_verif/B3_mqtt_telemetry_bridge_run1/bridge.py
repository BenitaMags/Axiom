"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for
from mqtt_sync_facade import _PublishResult, _SyncFacade


def _amqtt_client_factory_wrapper(cfg: BridgeConfig):
    """paho-mqtt's mqtt.Client(client_id=...) followed by client.connect(host, port)
    produced a synchronous client object ready for further synchronous calls like
    .publish(). amqtt has no synchronous client: amqtt.client.MQTTClient is
    async-only, constructed with a client_id and connected via an awaited
    `connect(uri)` coroutine using a "mqtt://host:port" URI instead of separate
    host/port arguments. This wrapper builds an amqtt MQTTClient, connects it on
    a persistent background event loop, and returns a _SyncFacade exposing the
    same synchronous publish()/close() surface the rest of this module calls,
    so callers don't need to be rewritten as async.
    """
    return _SyncFacade(cfg.client_id, cfg.host, cfg.port)


def _default_client(cfg: BridgeConfig):
    return _amqtt_client_factory_wrapper(cfg)


class TelemetryBridge:
    def __init__(self, cfg: BridgeConfig, client=None):
        self.cfg = cfg
        self.client = client if client is not None else _default_client(cfg)

    def _publish_and_wrap(self, topic: str, payload) -> dict:
        info = self.client.publish(topic, payload, qos=self.cfg.qos)
        return {
            "topic": topic,
            "payload": payload,
            "qos": self.cfg.qos,
            "rc": getattr(info, "rc", 0),
        }

    def publish_reading(self, tag: str, reading: dict) -> dict:
        topic = topic_for(self.cfg.base_topic, tag)
        payload = encode(reading)
        return self._publish_and_wrap(topic, payload)

    def publish_batch(self, readings: dict) -> list:
        """Publish {tag: reading} pairs, returning the per-message results."""
        return [self.publish_reading(tag, r) for tag, r in readings.items()]
