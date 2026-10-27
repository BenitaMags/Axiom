"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import json

from amqtt_bridge import build_amqtt_backed_client


class _AxiomPublishResult:
    """Small stand-in for paho-mqtt's MQTTMessageInfo, exposing `.rc` only."""

    def __init__(self, rc: int):
        self.rc = rc


class _AxiomMqttClientFacade:
    """Synchronous-looking facade around amqtt's async MQTTClient.

    paho-mqtt's `mqtt.Client()` + `.connect(host, port)` gives a synchronous
    object whose `.publish()` call returns immediately with an
    `MQTTMessageInfo` (an object carrying `.rc`, the result code). amqtt's
    `MQTTClient` is asyncio-only: `await client.connect(uri)` and
    `await client.publish(topic, payload, qos=qos)`, and the publish
    coroutine returns `None` on success (or raises) rather than handing back
    an `.rc`-bearing object.

    To bridge this without changing the synchronous call sites, this facade
    starts one persistent background event loop (a thread running
    `loop.run_forever()`) when it is constructed, connects amqtt's client on
    that loop, and keeps using the same loop/connection for every subsequent
    call -- unlike `asyncio.run`, which would tear the loop (and the
    connection bound to it) down after every single call. `.publish()`
    submits the amqtt publish coroutine to that loop and blocks for the
    result via `run_coroutine_threadsafe(...).result()`, which also
    re-raises any exception amqtt raised, so callers keep seeing failures
    exactly as they would from paho-mqtt (e.g. a bad topic raising
    ValueError) instead of having them silently swallowed. On success we can
    only truthfully report that amqtt accepted/queued the message (there is
    no ack for qos 0), so `.rc` is reported as 0 (paho-mqtt's
    MQTT_ERR_SUCCESS) purely to mean "no exception was raised", not "broker
    confirmed delivery".
    """

    def __init__(self, host: str = "localhost", port: int = 1883):
        self._runner, self._client = build_amqtt_backed_client(host, port)

    def _run(self, coro):
        return self._runner(coro)

    def publish(self, topic: str, payload: bytes, qos: int = 0) -> _AxiomPublishResult:
        self._run(self._client.publish(topic, payload, qos=qos))
        return _AxiomPublishResult(0)

    def close(self):
        self._run(self._client.disconnect())
        self._runner.stop()


def _axiom_mqtt_client_wrapper(host: str = "localhost", port: int = 1883):
    """Create a synchronous-looking MQTT client backed by amqtt.

    See `_AxiomMqttClientFacade` for the detailed rationale: paho-mqtt's
    `mqtt.Client()` + `.connect()` is synchronous and its `.publish()`
    returns an object with `.rc`; amqtt is async-only and its publish
    coroutine returns `None`/raises instead. This factory builds a facade
    that owns a persistent background event loop so the amqtt connection
    stays alive across calls, and exposes the same synchronous
    `.publish(topic, payload)` shape the rest of this module expects.
    """
    return _AxiomMqttClientFacade(host, port)


def _default_client(host: str = "localhost", port: int = 1883):
    return _axiom_mqtt_client_wrapper(host, port)


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
