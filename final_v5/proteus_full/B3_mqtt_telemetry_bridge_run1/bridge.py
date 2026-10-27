"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _PublishResult:
    """Mimics paho-mqtt's MQTTMessageInfo just enough for `.rc` access.

    paho-mqtt's ``publish()`` returns immediately with an ``MQTTMessageInfo``
    whose ``.rc`` is 0 on success (message queued/sent) and non-zero (e.g.
    MQTT_ERR_NO_CONN, MQTT_ERR_QUEUE_SIZE) when the client could not queue
    the message. amqtt's ``MQTTClient.publish()`` coroutine instead waits
    for the broker handshake (for QoS 1/2) and raises an exception on
    failure rather than returning a status code. We run that coroutine to
    completion and report rc=0 only if it actually finished without
    raising; if it raises a client/connection-level error we translate that
    into a non-zero rc so callers inspecting ``rc`` still observe the
    failure instead of a fabricated success.
    """

    def __init__(self, rc: int):
        self.rc = rc


class _AmqttSyncClient:
    """Synchronous facade over amqtt.client.MQTTClient.

    paho-mqtt.Client is synchronous: connect() blocks, publish() returns
    immediately with a status object. amqtt.client.MQTTClient is async-only:
    connect()/publish()/disconnect() are coroutines. This facade starts a
    persistent background event loop -- not a fresh asyncio.run() per call,
    which would tear the loop down after connect and leave the socket
    amqtt opened bound to a dead loop by the time publish() runs -- and
    exposes plain synchronous connect()/publish()/disconnect() methods that
    block on that loop via asyncio.run_coroutine_threadsafe(...).result(),
    which also re-raises whatever amqtt raised so failures reach the caller.
    """

    def __init__(self, client_id=None):
        import asyncio
        import threading
        from amqtt.client import MQTTClient

        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()
        self._client = MQTTClient(client_id=client_id) if client_id else MQTTClient()

    def _run(self, coro):
        import asyncio

        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def connect(self, host, port):
        uri = f"mqtt://{host}:{port}/"
        self._run(self._client.connect(uri))

    def publish(self, topic, payload, qos=0):
        from amqtt.client import ClientError, ConnectError, ProtocolHandlerError

        try:
            self._run(self._client.publish(topic, payload, qos=qos))
        except (ClientError, ConnectError, ProtocolHandlerError):
            return _PublishResult(1)
        return _PublishResult(0)

    def disconnect(self):
        self._run(self._client.disconnect())


def _axiom_mqtt_client_wrapper(cfg: BridgeConfig):
    """Build a synchronous-looking MQTT client backed by amqtt.

    paho-mqtt's ``mqtt.Client(client_id=...)`` followed by ``.connect(host,
    port)`` produces a synchronous client object usable immediately. amqtt
    has no synchronous client -- MQTTClient is async and must be driven
    from an event loop. This wrapper creates a persistent background loop
    (see _AmqttSyncClient) and connects the amqtt client through it,
    returning an object exposing the same connect/publish/disconnect
    surface that publish_reading() below relies on.
    """
    client = _AmqttSyncClient(client_id=cfg.client_id)
    client.connect(cfg.host, cfg.port)
    return client


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
