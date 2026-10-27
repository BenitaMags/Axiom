"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


def _axiom_mqtt_client_wrapper(cfg: BridgeConfig):
    """Bridge paho-mqtt's synchronous client construction to aiomqtt's async API.

    paho-mqtt: `mqtt.Client(client_id=...)` returns a client object
    immediately; a subsequent synchronous call to `.connect(host, port)`
    blocks until the TCP/MQTT handshake completes, after which the client is
    ready for synchronous `.publish()` calls. The caller (`_default_client`)
    returns this already-connected client object directly.

    aiomqtt: `aiomqtt.Client(hostname, port, identifier=...)` is an async
    context manager -- the connection is only actually established inside a
    coroutine, on `__aenter__`, and torn down on `__aexit__`. There is no
    synchronous constructor that hands back an "already connected" client.

    Transformation applied here: since `_default_client` must still return
    synchronously (its result is stored on `self.client` and used later by
    plain, non-async methods), this wrapper spins up a private asyncio event
    loop and manually drives `aiomqtt.Client.__aenter__` via
    `run_until_complete`, so the connection is established before the
    function returns. The event loop is stashed on the returned object so a
    future teardown path could drive `__aexit__` the same way.

    This is a best-effort shim, not a full fix: aiomqtt's intended usage is
    `async with aiomqtt.Client(...) as client:` scoping, which changes the
    ownership/lifetime model of the client in a way that a synchronous
    `return` statement fundamentally cannot express.
    """
    import asyncio
    import aiomqtt

    loop = asyncio.new_event_loop()
    client_cm = aiomqtt.Client(cfg.host, cfg.port, identifier=cfg.client_id)
    loop.run_until_complete(client_cm.__aenter__())
    client_cm._axiom_loop = loop
    return client_cm


def _default_client(cfg: BridgeConfig):
    import paho.mqtt.client as mqtt  # lazy import for testability, unused: see wrapper

    client = _axiom_mqtt_client_wrapper(cfg)
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
