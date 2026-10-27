# CHOSEN-TARGET: gmqtt
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _SyncGmqttClient:
    """A thin synchronous wrapper around gmqtt.Client.

    gmqtt is asyncio-based; this wrapper drives a private event loop so the
    rest of the codebase (and the test suite) can keep using a plain,
    synchronous API compatible with the previous paho-mqtt based client.
    """

    def __init__(self, client_id: str):
        import asyncio
        import gmqtt

        self._asyncio = asyncio
        self._gmqtt = gmqtt
        self._client = gmqtt.Client(client_id)
        self._loop = asyncio.new_event_loop()

    def connect(self, host: str, port: int):
        self._loop.run_until_complete(self._client.connect(host, port))

    def publish(self, topic, payload, qos=0):
        self._client.publish(topic, payload, qos=qos)

        class _Info:
            rc = 0

        return _Info()

    def disconnect(self):
        try:
            self._loop.run_until_complete(self._client.disconnect())
        except Exception:
            pass


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
