# CHOSEN-TARGET: gmqtt
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _PublishInfo:
    """Small shim so publish() has a `.rc` attribute like paho's MQTTMessageInfo."""

    __slots__ = ("rc",)

    def __init__(self, rc: int = 0):
        self.rc = rc


class _SyncGmqttClient:
    """
    Thin synchronous wrapper around gmqtt.Client so it can be used as a
    drop-in replacement for paho.mqtt.client.Client in this module.

    gmqtt is asyncio-based, so we drive a private event loop under the hood
    to preserve the synchronous, blocking API that TelemetryBridge relies on.
    """

    def __init__(self, cfg: BridgeConfig):
        import asyncio
        import gmqtt

        self._asyncio = asyncio
        self._cfg = cfg
        self._loop = asyncio.new_event_loop()
        self._client = gmqtt.Client(cfg.client_id)

        self._loop.run_until_complete(
            self._client.connect(cfg.host, cfg.port)
        )

    def publish(self, topic: str, payload: bytes, qos: int = 0):
        self._client.publish(topic, payload, qos=qos)
        return _PublishInfo(rc=0)

    def disconnect(self):
        try:
            self._loop.run_until_complete(self._client.disconnect())
        finally:
            self._loop.close()


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
