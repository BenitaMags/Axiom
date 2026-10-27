"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


def _default_client(cfg: BridgeConfig):
    import paho.mqtt.client as mqtt  # lazy import for testability

    client = mqtt.Client(client_id=cfg.client_id)
    client.connect(cfg.host, cfg.port)
    return client


class TelemetryBridge:
    def __init__(self, cfg: BridgeConfig, client=None):
        self.cfg = cfg
        if not (client is not None):
            self.client = _default_client(cfg)
        else:
            self.client = client

    def publish_reading(self, tag: str, reading: dict) -> dict:
        topic = topic_for(self.cfg.base_topic, tag)
        payload = encode(reading)
        _spt_r1 = {
            "topic": topic,
            "payload": payload,
            "qos": self.cfg.qos,
            "rc": getattr(self.client.publish(topic, payload, qos=self.cfg.qos), "rc", 0),
        }
        return _spt_r1

    def publish_batch(self, readings: dict) -> list:
        """Publish {tag: reading} pairs, returning the per-message results."""
        _spt_r2 = [self.publish_reading(tag, r) for tag, r in readings.items()]
        return _spt_r2
