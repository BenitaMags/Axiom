# CHOSEN-TARGET: gmqtt
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _SyncGmqttClient:
    """Thin synchronous adapter around gmqtt's asyncio-based client.

    gmqtt is asyncio-only, so this wrapper drives a private event loop to
    provide the same synchronous connect()/publish() surface that the
    previous paho-mqtt based client offered.
    """

    def __init__(self, cfg: BridgeConfig):
        import asyncio
        from gmqtt import Client as GmqttClient

        self._asyncio = asyncio
        self._cfg = cfg
        self._client = GmqttClient(cfg.client_id)
        self._loop = asyncio.new_event_loop()
        self._connected = False

    def connect(self, host: str, port: int):
        async def _connect():
            await self._client.connect(host, port)

        self._loop.run_until_complete(_connect())
        self._connected = True

    def publish(self, topic: str, payload: bytes, qos: int = 0):
        self._client.publish(topic, payload, qos=qos)

        class _Info:
            rc = 0

        return _Info()

    def __del__(self):
        try:
            if self._connected:
                async def _disconnect():
                    await self._client.disconnect()

                self._loop.run_until_complete(_disconnect())
            self._loop.close()
        except Exception:
            pass


def _default_client(cfg: BridgeConfig):
    client = _SyncGmqttClient(cfg)
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
