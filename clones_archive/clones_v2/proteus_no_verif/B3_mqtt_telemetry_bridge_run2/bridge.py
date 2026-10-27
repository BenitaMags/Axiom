"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


def _axiom_mqtt_client_wrapper(cfg: BridgeConfig):
    """Construct and connect an amqtt client, exposing a paho-mqtt-like
    synchronous publish() API for existing call sites.

    paho-mqtt's `mqtt.Client(client_id=...)` gives a synchronous object:
    you call `.connect(host, port)` (a blocking call) and later
    `.publish(topic, payload, qos=...)`, which returns a `MQTTMessageInfo`
    exposing `.rc` (a numeric return code, 0 == success) and `.mid`.

    amqtt's `amqtt.client.MQTTClient` is asyncio-native instead: it is
    constructed with just a client_id, connection is performed via
    `await client.connect(uri)` where `uri` is a `"mqtt://host:port"`
    string, and `await client.publish(...)` returns `None` on success
    (or raises an exception on failure) -- there is no `.rc`/`.mid`
    result object at all.

    This wrapper drives a dedicated asyncio event loop synchronously to
    construct and connect an amqtt `MQTTClient` using a composed
    `mqtt://host:port` URI, then returns a small adapter whose
    `.publish(topic, payload, qos=...)` method runs the amqtt publish
    coroutine to completion on that loop and translates the outcome
    (success/exception) into an object exposing `.rc` (0 on success,
    1 on failure) and `.mid` (always 0, since amqtt does not provide
    one), matching the shape existing callers of paho-mqtt expect.
    This masks, rather than resolves, the underlying sync/async
    mismatch, but it lets `TelemetryBridge` keep its synchronous
    `publish_reading`/`publish_batch` methods unchanged.
    """
    import asyncio
    from amqtt.client import MQTTClient

    class _PublishResult:
        def __init__(self, rc):
            self.rc = rc
            self.mid = 0

    class _SyncAmqttClient:
        def __init__(self, amqtt_client, loop):
            self._client = amqtt_client
            self._loop = loop

        def publish(self, topic, payload, qos=0):
            try:
                self._loop.run_until_complete(
                    self._client.publish(topic, payload, qos=qos)
                )
                return _PublishResult(0)
            except Exception:
                return _PublishResult(1)

    loop = asyncio.new_event_loop()
    amqtt_client = MQTTClient(client_id=cfg.client_id)
    uri = f"mqtt://{cfg.host}:{cfg.port}"
    loop.run_until_complete(amqtt_client.connect(uri))
    return _SyncAmqttClient(amqtt_client, loop)


def _default_client(cfg: BridgeConfig):

    return _axiom_mqtt_client_wrapper(cfg)


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
