# CHOSEN-TARGET: gmqtt
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _SyncClientAdapter:
    """Adapts an asyncio-based gmqtt client to the synchronous API this
    module's callers expect (mirrors the old paho.mqtt.client.Client usage).
    """

    def __init__(self, cfg: BridgeConfig):
        import asyncio
        from gmqtt import Client as GMQTTClient

        self._asyncio = asyncio
        self._cfg = cfg
        self._client = GMQTTClient(cfg.client_id)

        self._loop = asyncio.new_event_loop()
        self._loop.run_until_complete(
            self._client.connect(cfg.host, cfg.port)
        )

    def publish(self, topic, payload, qos=0):
        self._client.publish(topic, payload, qos=qos)

        class _Info:
            rc = 0

        return _Info()


def _default_client(cfg: BridgeConfig):
    return _SyncClientAdapter(cfg)


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
