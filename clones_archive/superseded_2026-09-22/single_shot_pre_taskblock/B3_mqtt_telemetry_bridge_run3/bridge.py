# CHOSEN-TARGET: gmqtt
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _SyncGmqttClient:
    """Thin synchronous wrapper around gmqtt's async client.

    gmqtt is asyncio-based; this adapter drives a private event loop so the
    public, synchronous API of TelemetryBridge does not have to change.
    """

    def __init__(self, cfg: BridgeConfig):
        import asyncio
        import gmqtt

        self._asyncio = asyncio
        self._gmqtt = gmqtt
        self._cfg = cfg
        self._loop = asyncio.new_event_loop()
        self._client = gmqtt.Client(cfg.client_id)
        self._loop.run_until_complete(self._client.connect(cfg.host, cfg.port))

    def publish(self, topic, payload, qos=0):
        self._client.publish(topic, payload, qos=qos)

        class _Info:
            rc = 0

        return _Info()

    def __del__(self):
        try:
            if self._client is not None:
                self._loop.run_until_complete(self._client.disconnect())
        except Exception:
            pass
        try:
            self._loop.close()
        except Exception:
            pass


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
