"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


def _axiom_mqtt_client_wrapper(cfg: BridgeConfig):
    """Bridge paho-mqtt's synchronous, persistent-connection client API onto aiomqtt.

    paho-mqtt's `mqtt.Client(...)` returns an object that keeps a connection
    open across many `.connect()` / `.publish()` calls made at arbitrary
    times, and `.publish()` returns an `MQTTMessageInfo`-like object exposing
    `.rc` synchronously.

    aiomqtt has no equivalent persistent, synchronous object: `aiomqtt.Client`
    is only usable as an async context manager, and the underlying connection
    is opened on `__aenter__` and torn down on `__aexit__`. There is no
    supported way to keep a single aiomqtt connection alive across independent
    synchronous calls the way paho-mqtt does.

    This wrapper approximates the paho-mqtt surface used by this module
    (`.connect(host, port)` followed by repeated `.publish(topic, payload,
    qos=...)`) by remembering the connection parameters on `.connect()`, and
    by opening a *fresh* `aiomqtt.Client` async context, publishing, and
    closing it again on every `.publish()` call, driven synchronously via
    `asyncio.run`. It returns a small object with an `.rc` attribute so
    callers that inspect `info.rc` keep working. This is only an
    approximation of paho-mqtt's semantics (a new connection per publish
    instead of one long-lived connection) -- it is not a true drop-in
    equivalent, but it is the closest bridge achievable with aiomqtt's API.
    """
    import asyncio

    import aiomqtt

    class _AxiomMqttClientWrapper:
        def __init__(self, cfg: BridgeConfig):
            self._cfg = cfg
            self._host = None
            self._port = None

        def connect(self, host, port):
            # aiomqtt has no standalone "connect and hold open" call; just
            # remember the target so publish() can open a short-lived
            # connection against it.
            self._host = host
            self._port = port

        def publish(self, topic, payload, qos=0):
            async def _do_publish():
                async with aiomqtt.Client(
                    hostname=self._host,
                    port=self._port,
                    identifier=self._cfg.client_id,
                ) as client:
                    await client.publish(topic, payload=payload, qos=qos)

            asyncio.run(_do_publish())

            class _PublishInfo:
                rc = 0

            return _PublishInfo()

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
