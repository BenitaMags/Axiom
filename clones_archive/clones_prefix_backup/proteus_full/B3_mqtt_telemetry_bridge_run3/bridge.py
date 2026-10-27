"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for

import asyncio


def _axiom_mqtt_client_wrapper(mqtt, cfg: BridgeConfig):
    """Bridge paho-mqtt's synchronous client object to aiomqtt's async client.

    paho-mqtt: ``mqtt.Client(client_id=...)`` returns a ready-to-use synchronous
    object immediately; ``.connect(host, port)`` and ``.publish(...)`` can then be
    called on it synchronously, any number of times, for the life of the object.

    aiomqtt: ``aiomqtt.Client(...)`` only becomes usable after entering its async
    context manager (``async with client: ...`` / ``__aenter__``), and every
    operation (connecting, publishing, ...) is a coroutine. There is no supported
    synchronous "construct once, use repeatedly" object.

    This wrapper constructs the aiomqtt client and a private event loop, and
    returns a small synchronous shim exposing ``.connect()``/``.publish()`` methods
    that proxy to ``_axiom_mqtt_connect_wrapper``/``_axiom_mqtt_publish_wrapper``,
    which drive the underlying coroutines to completion on that loop. This only
    partially replicates paho-mqtt's lifecycle: aiomqtt gives no guarantees about
    holding a connection open outside of the ``async with`` block, so behaviours
    like automatic reconnect or the background network loop are not reproduced.
    """
    loop = asyncio.new_event_loop()
    aio_client = mqtt.Client(hostname=cfg.host, port=cfg.port, identifier=cfg.client_id)

    class _AxiomSyncClientShim:
        def __init__(self):
            self._client = aio_client
            self._loop = loop

        def connect(self, host, port):
            return _axiom_mqtt_connect_wrapper(self._client, self._loop, host, port)

        def publish(self, topic, payload, qos=0):
            return _axiom_mqtt_publish_wrapper(self._client, self._loop, topic, payload, qos)

    return _AxiomSyncClientShim()


def _axiom_mqtt_connect_wrapper(client, loop, host, port):
    """Bridge paho-mqtt's synchronous ``client.connect(host, port)`` to aiomqtt.

    paho-mqtt: ``client.connect(host, port)`` blocks the calling thread until the
    TCP/MQTT handshake completes; afterwards the client object is connected and
    ready for further synchronous calls.

    aiomqtt: there is no ``connect(host, port)`` method -- connecting only happens
    by entering the client's async context manager (``__aenter__``), and aiomqtt
    does not officially support keeping that connection open beyond the
    ``async with`` block from synchronous code.

    This wrapper drives ``__aenter__`` on the given aiomqtt client using the
    supplied event loop, approximating a one-shot "connect now" call. Because it
    steps outside aiomqtt's intended ``async with`` pattern, aiomqtt gives no
    stability guarantees for the connection kept open this way afterwards.
    """
    loop.run_until_complete(client.__aenter__())
    return client


def _axiom_mqtt_publish_wrapper(client, loop, topic, payload, qos=0):
    """Bridge paho-mqtt's synchronous ``client.publish(...)`` to aiomqtt's coroutine.

    paho-mqtt: ``client.publish(topic, payload, qos=...)`` returns a
    ``MQTTMessageInfo`` object whose ``.rc`` attribute holds the result code (0
    means the message was queued/sent successfully).

    aiomqtt: ``client.publish(topic, payload, qos=...)`` is a coroutine returning
    ``None`` on success and raising an exception (e.g. ``aiomqtt.MqttError``) on
    failure -- there is no returned status object to inspect.

    This wrapper runs that coroutine to completion on the supplied event loop and,
    since aiomqtt gives back nothing useful on success, fabricates a tiny object
    with an ``.rc`` attribute hard-coded to 0 (mirroring paho-mqtt's success code)
    so existing code reading ``info.rc`` keeps working unchanged.
    """
    loop.run_until_complete(client.publish(topic, payload, qos=qos))

    class _AxiomPublishResult:
        rc = 0

    return _AxiomPublishResult()


def _default_client(cfg: BridgeConfig):
    import paho.mqtt.client as mqtt  # lazy import for testability

    client = _axiom_mqtt_client_wrapper(mqtt, cfg)
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
