# CHOSEN-TARGET: gmqtt
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _SyncGmqttClient:
    """Thin synchronous wrapper around gmqtt's asyncio-based client.

    paho-mqtt's `Client` exposes a synchronous `connect()`/`publish()` API.
    gmqtt is asyncio-native, so this adapter runs a dedicated event loop
    in a background thread and exposes the same synchronous surface that
    `TelemetryBridge` expects (a `.publish(topic, payload, qos=...)` call
    returning an object with an `.rc` attribute).
    """

    def __init__(self, cfg: BridgeConfig):
        import asyncio
        import threading
        import gmqtt

        self._asyncio = asyncio
        self._cfg = cfg
        self._loop = asyncio.new_event_loop()
        self._client = gmqtt.Client(cfg.client_id)

        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        fut = asyncio.run_coroutine_threadsafe(
            self._client.connect(cfg.host, cfg.port), self._loop
        )
        fut.result()

    def _run_loop(self):
        self._asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def publish(self, topic, payload, qos=0):
        self._client.publish(topic, payload, qos=qos)
        return _PublishInfo(rc=0)

    def disconnect(self):
        fut = self._asyncio.run_coroutine_threadsafe(
            self._client.disconnect(), self._loop
        )
        fut.result()
        self._loop.call_soon_threadsafe(self._loop.stop)


class _PublishInfo:
    def __init__(self, rc=0):
        self.rc = rc


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
