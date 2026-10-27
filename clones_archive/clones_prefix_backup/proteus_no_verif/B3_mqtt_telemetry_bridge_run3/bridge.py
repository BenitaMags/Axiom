"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
import asyncio

from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


def _axiom_mqtt_client_wrapper(cfg: BridgeConfig):
    """Bridge paho-mqtt's synchronous connect()-once/publish-many client object
    to aiomqtt's async, context-managed `Client`.

    paho-mqtt's `Client.connect()` establishes a persistent connection once, and
    subsequent `Client.publish()` calls return a `MQTTMessageInfo`-like object
    exposing an `.rc` attribute (result code) synchronously, reusing that same
    connection for the lifetime of the object.

    aiomqtt has no persistent, synchronous client object: `aiomqtt.Client` is an
    async context manager, and `connect()`/`publish()` are coroutines that must
    be awaited inside `async with aiomqtt.Client(...) as client:`. There is no
    way to keep a connection open across independent synchronous calls without
    an externally managed event loop.

    This wrapper approximates the old API surface by exposing synchronous
    `connect()` (a no-op that just records host/port, since aiomqtt has no
    standalone connect-and-hold step) and `publish()` methods. Each `publish()`
    call opens a brand-new aiomqtt connection via `asyncio.run(...)`, publishes
    the single message, and closes it again -- it cannot replicate the
    persistent-connection model, but it lets call sites keep using a
    `client.publish(...)` call returning an object with an `.rc` attribute,
    synthesized as 0 on success and 1 if the publish raised an exception.
    """
    import aiomqtt

    class _AxiomMqttClientWrapper:
        def __init__(self, cfg):
            self._cfg = cfg
            self._host = None
            self._port = None

        def connect(self, host, port):
            # aiomqtt has no persistent, synchronous "connect once" step; the
            # connection is only ever opened for the lifetime of an
            # `async with aiomqtt.Client(...)` block. We just remember the
            # target address here for later use in publish().
            self._host = host
            self._port = port

        def publish(self, topic, payload, qos=0):
            async def _do_publish():
                async with aiomqtt.Client(
                    hostname=self._host,
                    port=self._port,
                    identifier=self._cfg.client_id,
                ) as client:
                    await client.publish(topic, payload, qos=qos)

            class _Info:
                rc = 0

            try:
                asyncio.run(_do_publish())
            except Exception:
                _Info.rc = 1
            return _Info()

    return _AxiomMqttClientWrapper(cfg)


def _default_client(cfg: BridgeConfig):
    client = _axiom_mqtt_client_wrapper(cfg)
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
