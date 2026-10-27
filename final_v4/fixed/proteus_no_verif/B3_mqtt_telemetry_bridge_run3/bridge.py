"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `amqtt` (async, coroutine-based). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
import asyncio

from mqtt_config import BridgeConfig
from telemetry import encode, topic_for

from amqtt_bridge_support import PublishResult, SyncAmqttClient


# Kept for backwards compatibility: some callers/tests may reference the
# original private name directly.
_PublishResult = PublishResult
_SyncAmqttClient = SyncAmqttClient


def _axiom_mqtt_client_wrapper(cfg: BridgeConfig):
    """Build a synchronous-looking amqtt client to replace paho-mqtt's
    ``mqtt.Client(client_id=...)`` followed by ``client.connect(host, port)``.

    Returns an already-connected ``SyncAmqttClient`` so callers keep using
    ``client.publish(topic, payload, qos=...)`` and reading ``.rc`` off the
    result exactly as they did with paho-mqtt.
    """
    client = SyncAmqttClient(cfg)
    client.connect(cfg.host, cfg.port)
    return client


def _default_client(cfg: BridgeConfig):
    return _axiom_mqtt_client_wrapper(cfg)


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
