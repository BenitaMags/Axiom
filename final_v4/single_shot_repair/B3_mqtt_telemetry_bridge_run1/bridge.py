# CHOSEN-TARGET: amqtt
"""bridge -- publish sensor telemetry to an MQTT broker.



require glue -- hence Category B. The client is dependency-injected for testing.
"""
import asyncio

from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _AmqttClientAdapter:
    """Adapter that exposes a paho-like `publish(topic, payload, qos)` API
    on top of amqtt's asyncio-based MQTTClient."""

    def __init__(self, cfg: BridgeConfig):
        from amqtt.client import MQTTClient

        self.cfg = cfg
        self._loop = asyncio.new_event_loop()
        self._client = MQTTClient(client_id=cfg.client_id, loop=self._loop)
        uri = f"mqtt://{cfg.host}:{cfg.port}/"
        self._loop.run_until_complete(self._client.connect(uri))

    def publish(self, topic, payload, qos=0):
        from amqtt.client import QOS_0, QOS_1, QOS_2

        qos_map = {0: QOS_0, 1: QOS_1, 2: QOS_2}
        hb_qos = qos_map.get(qos, QOS_0)

        rc = 0
        try:
            self._loop.run_until_complete(
                self._client.publish(topic, payload, qos=hb_qos)
            )
        except Exception:
            rc = 1

        return _PublishInfo(rc)

    def __del__(self):
        try:
            self._loop.run_until_complete(self._client.disconnect())
        except Exception:
            pass
        try:
            self._loop.close()
        except Exception:
            pass


class _PublishInfo:
    def __init__(self, rc):
        self.rc = rc


def _default_client(cfg: BridgeConfig):
    return _AmqttClientAdapter(cfg)


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
