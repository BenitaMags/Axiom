# CHOSEN-TARGET: gmqtt
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _PublishResult:
    __slots__ = ("rc",)

    def __init__(self, rc=0):
        self.rc = rc


class _GmqttClientAdapter:
    """Thin synchronous adapter around gmqtt's async client.

    gmqtt is asyncio-based, so this adapter manages a private event loop
    and drives the connect/publish calls synchronously to preserve the
    original paho-mqtt-based synchronous API surface used by TelemetryBridge.
    """

    def __init__(self, cfg: BridgeConfig):
        import asyncio
        import gmqtt

        self._asyncio = asyncio
        self._gmqtt = gmqtt
        self._cfg = cfg
        self._loop = asyncio.new_event_loop()
        self._client = gmqtt.Client(cfg.client_id)
        self._connected = False

    def connect(self, host, port):
        async def _do_connect():
            await self._client.connect(host, port)

        self._loop.run_until_complete(_do_connect())
        self._connected = True

    def publish(self, topic, payload, qos=0):
        self._client.publish(topic, payload, qos=qos)
        return _PublishResult(rc=0)

    def __del__(self):
        try:
            if self._connected:
                async def _do_disconnect():
                    await self._client.disconnect()

                self._loop.run_until_complete(_do_disconnect())
        except Exception:
            pass
        try:
            self._loop.close()
        except Exception:
            pass


def _default_client(cfg: BridgeConfig):
    client = _GmqttClientAdapter(cfg)
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
