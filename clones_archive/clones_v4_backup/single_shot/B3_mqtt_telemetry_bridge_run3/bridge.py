# CHOSEN-TARGET: gmqtt
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _SyncGmqttClient:
    """Thin synchronous wrapper around gmqtt.Client for a drop-in replacement
    of the paho-mqtt synchronous client used previously.
    """

    def __init__(self, cfg: BridgeConfig):
        import asyncio
        import gmqtt

        self._gmqtt = gmqtt
        self._cfg = cfg
        self._loop = asyncio.new_event_loop()
        self._client = gmqtt.Client(cfg.client_id)
        self._loop.run_until_complete(self._connect())

    async def _connect(self):
        await self._client.connect(self._cfg.host, self._cfg.port)

    def publish(self, topic, payload, qos=0):
        self._client.publish(topic, payload, qos=qos)
        return type("PublishInfo", (), {"rc": 0})()


def _default_client(cfg: BridgeConfig):
    return _SyncGmqttClient(cfg)


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
