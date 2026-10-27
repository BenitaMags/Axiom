"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


def _axiom_mqtt_client_factory_wrapper(cfg: BridgeConfig):
    """Bridge paho-mqtt's synchronous client construction to amqtt's async API.

    paho-mqtt's ``mqtt.Client(client_id=...)`` returns an object that is
    immediately usable: ``client.connect(host, port)`` blocks the calling
    thread until the handshake finishes, and later ``client.publish(...)``
    returns a ``MessageInfo`` object exposing a synchronous ``.rc`` status
    code.

    amqtt's ``amqtt.client.MQTTClient`` is asynchronous instead: creating
    the instance performs no I/O, ``connect()`` is a coroutine that must be
    awaited, and ``publish()`` is a coroutine that resolves to ``None`` (no
    ``.rc`` attribute at all).

    This wrapper constructs an ``amqtt.client.MQTTClient`` and then drives
    its ``connect()`` coroutine to completion synchronously using an
    internally-managed event loop, so that ``_default_client`` can keep
    returning a fully "ready" client object to synchronous callers, just as
    the old paho-mqtt code path did. It also monkey-patches a synchronous
    ``publish`` method onto the returned client that runs amqtt's async
    ``publish()`` coroutine through that same loop and fabricates a small
    result object with an ``.rc`` attribute (always ``0`` on success), so
    that existing call sites doing ``getattr(info, "rc", 0)`` keep working
    without further changes.
    """
    import asyncio

    from amqtt.client import MQTTClient

    loop = asyncio.new_event_loop()
    client = MQTTClient(client_id=cfg.client_id)
    uri = f"mqtt://{cfg.host}:{cfg.port}/"
    loop.run_until_complete(client.connect(uri))

    class _AxiomPublishResult:
        def __init__(self, rc=0):
            self.rc = rc

    def _sync_publish(topic, payload, qos=0):
        loop.run_until_complete(client.publish(topic, payload, qos=qos))
        return _AxiomPublishResult(0)

    client.publish = _sync_publish
    return client


def _default_client(cfg: BridgeConfig):
    import paho.mqtt.client as mqtt  # lazy import for testability

    return _axiom_mqtt_client_factory_wrapper(cfg)


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
