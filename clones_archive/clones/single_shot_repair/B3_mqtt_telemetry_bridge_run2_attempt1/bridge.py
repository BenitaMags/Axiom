# CHOSEN-TARGET: gmqtt
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


def _default_client(cfg: BridgeConfig):
    import asyncio
    import gmqtt

    class _SyncClientWrapper:
        """Wraps an asyncio-based gmqtt client to expose a synchronous,
        paho-like `publish(topic, payload, qos=...)` API used by
        TelemetryBridge.
        """

        def __init__(self, client_id, host, port):
            self._client_id = client_id
            self._host = host
            self._port = port
            self._loop = asyncio.new_event_loop()
            self._client = gmqtt.Client(client_id)
            self._connected = False
            self._connect()

        def _connect(self):
            async def _do_connect():
                await self._client.connect(self._host, self._port)

            self._loop.run_until_complete(_do_connect())
            self._connected = True

        def publish(self, topic, payload, qos=0):
            self._client.publish(topic, payload, qos=qos)

            class _Info:
                rc = 0

            return _Info()

    return _SyncClientWrapper(cfg.client_id, cfg.host, cfg.port)


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
