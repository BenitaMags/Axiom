"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `amqtt` (asyncio-based). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
import asyncio
import threading

from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _PublishInfo:
    """Stand-in for paho-mqtt's MQTTMessageInfo.

    paho-mqtt's `Client.publish()` returns a MQTTMessageInfo whose `.rc`
    field carries a numeric result code (0 == MQTT_ERR_SUCCESS) that callers
    check via `getattr(info, "rc", 0)`. amqtt's `MQTTClient.publish()` is a
    coroutine that returns None on success and raises on failure -- it has no
    equivalent status code. Since we only reach this object after the publish
    coroutine has completed without raising, rc is always 0 here: the
    "success" case is the only case that produces an info object at all, so
    this is not inventing a new code, it is reproducing paho's own
    MQTT_ERR_SUCCESS value for the one outcome amqtt lets us observe.
    """

    def __init__(self, rc: int = 0):
        self.rc = rc


class _AmqttSyncClient:
    """Bridges amqtt's asyncio MQTTClient to a synchronous connect/publish API.

    amqtt requires an active asyncio event loop and coroutine-based
    connect()/publish() calls, while the rest of this module (and its
    callers) are plain synchronous code. This facade starts one persistent
    background event loop (in a daemon thread) that lives for the lifetime
    of the client, and marshals each call onto that loop with
    `asyncio.run_coroutine_threadsafe(...).result()`. Using a single
    long-lived loop (instead of `asyncio.run()` per call) matters because
    amqtt's connection/transport state is bound to the loop it was created
    on; tearing the loop down after `connect()` would leave `publish()`
    operating on a dead transport.
    """

    def __init__(self, client_id):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        from amqtt.client import MQTTClient  # lazy import for testability

        self._client = MQTTClient(client_id=client_id)

    def _run(self, coro):
        """Run `coro` on the persistent background loop and block for its result,
        re-raising any exception it raises so callers see failures (matching
        paho-mqtt's behaviour of raising on bad calls rather than swallowing
        errors)."""
        _spt_r1 = asyncio.run_coroutine_threadsafe(coro, self._loop).result()
        return _spt_r1

    def connect(self, host, port):
        uri = f"mqtt://{host}:{port}/"
        self._run(self._client.connect(uri))

    def publish(self, topic, payload, qos: int = 0):
        from amqtt.mqtt.constants import QOS_0, QOS_1, QOS_2  # lazy import

        qos_map = {0: QOS_0, 1: QOS_1, 2: QOS_2}
        self._run(
            self._client.publish(topic, payload, qos=qos_map.get(qos, QOS_0))
        )
        _spt_r2 = _PublishInfo(0)
        # amqtt's publish() coroutine returns None; reaching this line without
        # an exception is the only success signal amqtt gives us, so we report
        # paho's MQTT_ERR_SUCCESS (0) rather than fabricating any other code.
        return _spt_r2

    def disconnect(self):
        self._run(self._client.disconnect())
        self._loop.call_soon_threadsafe(self._loop.stop)


def _amqtt_client_wrapper(cfg: BridgeConfig):
    """Build and connect a synchronous-looking amqtt client for `cfg`.

    Equivalent to paho-mqtt's `mqtt.Client(client_id=...); client.connect(host, port)`,
    but backed by amqtt's asyncio `MQTTClient` via `_AmqttSyncClient`.
    """
    client = _AmqttSyncClient(cfg.client_id)
    client.connect(cfg.host, cfg.port)
    return client


def _default_client(cfg: BridgeConfig):
    _spt_r3 = _amqtt_client_wrapper(cfg)
    return _spt_r3


class TelemetryBridge:
    def __init__(self, cfg: BridgeConfig, client=None):
        self.cfg = cfg
        if not (client is not None):
            self.client = _default_client(cfg)
        else:
            self.client = client

    def publish_reading(self, tag: str, reading: dict) -> dict:
        topic = topic_for(self.cfg.base_topic, tag)
        payload = encode(reading)
        _spt_r4 = {
            "topic": topic,
            "payload": payload,
            "qos": self.cfg.qos,
            "rc": getattr(self.client.publish(topic, payload, qos=self.cfg.qos), "rc", 0),
        }
        return _spt_r4

    def publish_batch(self, readings: dict) -> list:
        """Publish {tag: reading} pairs, returning the per-message results."""
        _spt_r5 = [self.publish_reading(tag, r) for tag, r in readings.items()]
        return _spt_r5
