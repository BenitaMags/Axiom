# CHOSEN-TARGET: gmqtt
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _SyncGmqttClient:
    """Thin synchronous wrapper around gmqtt's asyncio-based client.

    gmqtt is asyncio-native, but the rest of this codebase (and its test
    suite) expects a synchronous, paho-like ``connect``/``publish`` API.
    This adapter runs the necessary coroutines on a private event loop so
    callers can keep using it synchronously.
    """

    def __init__(self, client_id: str):
        import asyncio
        from gmqtt import Client as GmqttClient

        self._asyncio = asyncio
        self._loop = asyncio.new_event_loop()
        self._client = GmqttClient(client_id)

    def connect(self, host: str, port: int):
        self._loop.run_until_complete(self._client.connect(host, port))

    def publish(self, topic: str, payload: bytes, qos: int = 0):
        self._client.publish(topic, payload, qos=qos)
        return _PublishInfo(rc=0)

    def disconnect(self):
        try:
            self._loop.run_until_complete(self._client.disconnect())
        finally:
            self._loop.close()


class _PublishInfo:
    def __init__(self, rc: int = 0):
        self.rc = rc


def _default_client(cfg: BridgeConfig):
    client = _SyncGmqttClient(client_id=cfg.client_id)
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
