"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


# ──────────────────────────────────────────────────────────────────────
# AXIOM CONNECTOR: paho-mqtt → aiomqtt
# The following functions bridge API incompatibilities between
# paho-mqtt and aiomqtt. They are injected by the AXIOM
# connector agent and called in place of the original API.
# ──────────────────────────────────────────────────────────────────────
def _create_aiomqtt_compat_client(client_id):
    """Compatibility factory replacing paho.mqtt.client.Client(client_id=...).

    Original: `mqtt.Client(client_id=cfg.client_id)` returns a synchronous,
    callback-based paho-mqtt Client object. Callers then invoke `.connect(host, port)`
    (blocking, synchronous) and `.publish(topic, payload, qos)` (synchronous, returns
    an MQTTMessageInfo-like object with an `.rc` attribute) directly on that object.

    New: aiomqtt.Client is async-context-managed -- `async with aiomqtt.Client(...) as c:`
    and all I/O (`connect`, `publish`) is a coroutine. This wrapper returns a
    drop-in synchronous-looking object exposing `.connect(host, port)` and
    `.publish(topic, payload, qos=0)` methods (plus `.disconnect()`), so existing
    call sites (`client.connect(...)`, `client.publish(...)`) keep working unchanged.

    Internally it owns a private asyncio event loop, drives the aiomqtt async
    context manager's `__aenter__`/`__aexit__` manually via `run_until_complete`,
    and synchronously awaits `publish()` calls, wrapping the (no return value)
    result in a small object exposing `.rc = 0` to mimic paho's MQTTMessageInfo
    contract expected by callers such as `getattr(info, "rc", 0)`.
    """
    import asyncio
    import aiomqtt

    class _CompatClient:
        def __init__(self, client_id):
            self._client_id = client_id
            self._loop = asyncio.new_event_loop()
            self._aiomqtt_ctx = None
            self._aiomqtt_client = None

        def connect(self, host, port):
            async def _connect():
                self._aiomqtt_ctx = aiomqtt.Client(
                    hostname=host, port=port, identifier=self._client_id
                )
                self._aiomqtt_client = await self._aiomqtt_ctx.__aenter__()

            self._loop.run_until_complete(_connect())
            return self

        def publish(self, topic, payload, qos=0):
            async def _publish():
                await self._aiomqtt_client.publish(topic, payload=payload, qos=qos)

            self._loop.run_until_complete(_publish())

            class _Info:
                rc = 0

            return _Info()

        def disconnect(self):
            if self._aiomqtt_ctx is not None:
                self._loop.run_until_complete(
                    self._aiomqtt_ctx.__aexit__(None, None, None)
                )
            if not self._loop.is_closed():
                self._loop.close()

    return _CompatClient(client_id)




def _default_client(cfg: BridgeConfig):

    client = _create_aiomqtt_compat_client(client_id=cfg.client_id)
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