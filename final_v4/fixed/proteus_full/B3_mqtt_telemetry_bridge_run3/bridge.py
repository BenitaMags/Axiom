"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
import asyncio
import threading

from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _Result:
    """Mimics paho-mqtt's `MQTTMessageInfo` enough for callers that read `.rc`.

    paho-mqtt's `Client.publish()` returns an `MQTTMessageInfo` whose `.rc`
    attribute is 0 on success and a nonzero `MQTT_ERR_*` code otherwise.
    amqtt's `MQTTClient.publish()` is a coroutine that returns `None` (or,
    for QoS >= 1, returns once the broker has acknowledged the message) and
    raises on failure instead of returning a status code. This class is the
    object we hand back after awaiting amqtt's publish and mapping the
    outcome onto paho-mqtt's rc convention.
    """

    def __init__(self, rc: int):
        self.rc = rc


class _axiom_mqtt_client_wrapper:
    """Adapts amqtt's async `MQTTClient` to paho-mqtt's sync connect/publish API.

    paho-mqtt's `Client` is synchronous: `.connect()` blocks until the
    TCP/MQTT handshake completes, and `.publish()` returns immediately with
    an `MQTTMessageInfo` carrying a `.rc` status code.

    amqtt only exposes an async API (`await client.connect(uri)`,
    `await client.publish(topic, msg, qos)`), so this wrapper starts one
    persistent background event loop (kept alive for the object's whole
    lifetime, never a fresh loop per call) and marshals each call onto it
    with `asyncio.run_coroutine_threadsafe(...).result()`. Keeping the loop
    alive keeps the connection's transport valid across calls, and
    `.result()` re-raises whatever amqtt raised so genuine failures reach
    the caller instead of being hidden behind a fabricated status.

    For QoS 0, amqtt's publish returns as soon as the message is queued --
    no delivery confirmation is available, the same as paho-mqtt's QoS 0.
    For QoS >= 1, amqtt awaits the broker's PUBACK/PUBREC handshake before
    returning, so a successful await really does mean the broker
    acknowledged the message.
    """

    def __init__(self, cfg: BridgeConfig):
        from amqtt.client import MQTTClient

        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()
        self._client = MQTTClient(client_id=cfg.client_id)
        uri = f"mqtt://{cfg.host}:{cfg.port}"
        self._run(self._client.connect(uri))

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def publish(self, topic, payload, qos=0):
        """Publish and return a `_Result` carrying an `.rc` status code.

        amqtt's `publish()` returns `None` on success (queued for QoS 0,
        broker-acknowledged for QoS >= 1) and raises on transport or
        protocol failure rather than returning a paho-style rc. We await it
        on the persistent loop and translate: success -> rc=0.

        amqtt does not export a stable, documented public exception class
        for "not connected"/"broker unreachable" from `amqtt.client` on this
        distribution (attempting to import `ClientException` or
        `ConnectException` from it raises `ImportError`), so rather than
        reference a name that may not exist we catch the network-layer
        failure modes paho-mqtt itself reports at this call site --
        `ConnectionError` and `OSError` (socket refusal, reset, broken pipe,
        timeouts) -- and map them onto rc=4 (`MQTT_ERR_NO_CONN`), the same
        code paho-mqtt returns when a publish cannot reach the broker. Any
        other exception (e.g. a malformed topic) propagates unchanged, just
        as paho-mqtt lets unexpected errors reach the caller.
        """
        try:
            self._run(self._client.publish(topic, payload, qos=qos))
        except (ConnectionError, OSError):
            return _Result(4)
        return _Result(0)


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
