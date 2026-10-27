# CHOSEN-TARGET: gmqtt
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _GmqttClientAdapter:
    """Adapter exposing a paho-like synchronous publish() API on top of gmqtt.

    gmqtt is asyncio-based, so we drive a background event loop thread and
    a persistent connection, exposing a simple synchronous `publish()`
    method and `.rc` style result compatible with the previous paho usage.
    """

    def __init__(self, cfg: BridgeConfig):
        import asyncio
        import threading
        import gmqtt

        self._gmqtt = gmqtt
        self._cfg = cfg
        self._loop = asyncio.new_event_loop()
        self._client = gmqtt.Client(cfg.client_id)
        self._connected = threading.Event()

        def _run_loop():
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()

        self._thread = threading.Thread(target=_run_loop, daemon=True)
        self._thread.start()

        fut = asyncio.run_coroutine_threadsafe(self._connect(), self._loop)
        fut.result()

    async def _connect(self):
        await self._client.connect(self._cfg.host, self._cfg.port)

    def publish(self, topic: str, payload: bytes, qos: int = 0):
        import asyncio

        async def _do_publish():
            self._client.publish(topic, payload, qos=qos)

        fut = asyncio.run_coroutine_threadsafe(_do_publish(), self._loop)
        fut.result()

        class _PublishInfo:
            rc = 0

        return _PublishInfo()


def _default_client(cfg: BridgeConfig):
    return _GmqttClientAdapter(cfg)


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
