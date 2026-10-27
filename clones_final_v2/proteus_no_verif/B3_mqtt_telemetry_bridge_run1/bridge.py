"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
import asyncio

from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _AxiomPublishResult:
    """Synchronous stand-in for paho-mqtt's MQTTMessageInfo.

    paho-mqtt's `Client.publish()` returns an `MQTTMessageInfo` object with an
    `.rc` attribute indicating the result code of the publish call. amqtt's
    `MQTTClient.publish()` is a coroutine that returns nothing on success and
    raises an exception on failure. This tiny shim gives callers the `.rc`
    attribute they expect, set to 0 when the underlying coroutine completed
    without raising.
    """

    def __init__(self, rc: int = 0):
        self.rc = rc


class _axiom_amqtt_client_wrapper:
    """Synchronous facade over amqtt's async MQTTClient.

    paho-mqtt's `mqtt.Client` exposes synchronous `.connect(host, port)` and
    `.publish(topic, payload, qos=...)` methods that block until the
    operation is issued, with `.publish()` returning an object with an `.rc`
    attribute. amqtt's `MQTTClient` instead exposes coroutines
    (`connect(uri)`, `publish(topic, message, qos)`, `disconnect()`) that must
    be awaited inside an event loop.

    This wrapper preserves the synchronous call contract used by
    `publish_reading`/`publish_batch` by driving a single persistent asyncio
    event loop internally and running each amqtt coroutine to completion via
    `loop.run_until_complete(...)`, translating the coroutine results into the
    synchronous return shapes (e.g. `_AxiomPublishResult` with `.rc`) that the
    rest of this module expects.
    """

    _QOS_MAP = {0: 0x00, 1: 0x01, 2: 0x02}

    def __init__(self, client_id: str):
        from amqtt.client import MQTTClient

        self._loop = asyncio.new_event_loop()
        self._client = MQTTClient(client_id=client_id)

    def connect(self, host: str, port: int):
        _spt_r1 = self._loop.run_until_complete(self._client.connect(f"mqtt://{host}:{port}"))
        return _spt_r1

    def publish(self, topic: str, payload, qos: int = 0):
        amqtt_qos = self._QOS_MAP.get(qos, 0x00)
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        try:
            self._loop.run_until_complete(
                self._client.publish(topic, payload, qos=amqtt_qos)
            )
            _spt_r2 = _AxiomPublishResult(rc=0)
            return _spt_r2
        except Exception:
            _spt_r3 = _AxiomPublishResult(rc=1)
            return _spt_r3

    def disconnect(self):
        _spt_r4 = self._loop.run_until_complete(self._client.disconnect())
        return _spt_r4


def _default_client(cfg: BridgeConfig):
    client = _axiom_amqtt_client_wrapper(client_id=cfg.client_id)
    client.connect(cfg.host, cfg.port)
    return client


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
        _spt_r5 = {
            "topic": topic,
            "payload": payload,
            "qos": self.cfg.qos,
            "rc": getattr(self.client.publish(topic, payload, qos=self.cfg.qos), "rc", 0),
        }
        return _spt_r5

    def publish_batch(self, readings: dict) -> list:
        """Publish {tag: reading} pairs, returning the per-message results."""
        _spt_r6 = [self.publish_reading(tag, r) for tag, r in readings.items()]
        return _spt_r6
