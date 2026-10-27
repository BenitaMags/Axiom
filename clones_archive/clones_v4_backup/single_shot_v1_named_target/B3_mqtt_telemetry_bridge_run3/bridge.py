"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.

Since aiomqtt is async-only and the public API of `TelemetryBridge` must remain
synchronous (same signatures/return types as before), a small adapter runs the
async aiomqtt client on a background event loop and exposes a synchronous
`publish(topic, payload, qos=...)` method mirroring the paho-mqtt shape
(an object with an `.rc` attribute), so the rest of the code is unchanged.
"""
import asyncio
import threading

from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _PublishResult:
    """Mimics paho-mqtt's MQTTMessageInfo enough for our purposes."""

    def __init__(self, rc: int = 0):
        self.rc = rc


class _AiomqttClientAdapter:
    """Wraps an aiomqtt.Client in a synchronous interface.

    Runs its own event loop on a background thread, keeps a single aiomqtt
    connection open for the lifetime of the adapter, and exposes a
    synchronous `publish` method compatible with the previous paho-mqtt usage.
    """

    def __init__(self, cfg: BridgeConfig):
        import aiomqtt  # lazy import for testability

        self._aiomqtt = aiomqtt
        self.cfg = cfg

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        self._client_cm = None
        self._client = None

        fut = asyncio.run_coroutine_threadsafe(self._connect(), self._loop)
        fut.result()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _connect(self):
        self._client_cm = self._aiomqtt.Client(
            hostname=self.cfg.host,
            port=self.cfg.port,
            identifier=self.cfg.client_id,
        )
        self._client = await self._client_cm.__aenter__()

    async def _publish_async(self, topic, payload, qos):
        await self._client.publish(topic, payload=payload, qos=qos)

    def publish(self, topic: str, payload: bytes, qos: int = 0):
        fut = asyncio.run_coroutine_threadsafe(
            self._publish_async(topic, payload, qos), self._loop
        )
        try:
            fut.result()
            rc = 0
        except Exception:
            rc = 1
        return _PublishResult(rc=rc)

    def __del__(self):
        try:
            if self._client_cm is not None:
                fut = asyncio.run_coroutine_threadsafe(
                    self._client_cm.__aexit__(None, None, None), self._loop
                )
                fut.result(timeout=5)
        except Exception:
            pass
        finally:
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass


def _default_client(cfg: BridgeConfig):
    return _AiomqttClientAdapter(cfg)


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
