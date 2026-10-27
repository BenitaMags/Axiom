"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `amqtt` (asyncio-based, async-context-managed). The connection/publish models
differ enough to require glue -- hence Category B. The client is
dependency-injected for testing.
"""
import asyncio

from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _PublishResult:
    """Stand-in for paho-mqtt's `MessageInfo`.

    paho-mqtt's synchronous `Client.publish()` returns a `MessageInfo` object
    whose `.rc` attribute holds the MQTT return code (0 == MQTT_ERR_SUCCESS).
    amqtt's coroutine `MQTTClient.publish()` has no return value on success --
    it simply completes, or raises an exception on failure. To keep
    `publish_reading`'s existing `getattr(info, "rc", 0)` access working
    unchanged, we wrap a successful amqtt publish in this object with
    `rc=0`. We never fabricate a non-zero rc: any amqtt failure raises an
    exception (see `_amqtt_client_wrapper.publish`) rather than being coerced
    into a return code, since paho-mqtt itself raises for the equivalent
    failures (e.g. invalid topic) rather than returning a nonzero rc.
    """

    def __init__(self, rc: int = 0):
        self.rc = rc


class _amqtt_client_wrapper:
    """Synchronous facade over amqtt's asyncio-based `MQTTClient`.

    paho-mqtt's `mqtt.Client` exposes ordinary blocking methods:
    `.connect(host, port)` and `.publish(topic, payload, qos=...)` both run
    to completion (or raise) before returning control to the caller. amqtt's
    `MQTTClient` instead exposes coroutines (`connect(uri)`,
    `publish(topic, message, qos=...)`) that must be awaited inside an
    event loop.

    `TelemetryBridge` is a synchronous class by contract (it is constructed
    and used from synchronous code, with a dependency-injected client for
    testing), so restructuring it into `async def` methods driven by an
    external event loop is out of scope for a wrapper around a single call
    site. Instead, this wrapper owns a private event loop and drives each
    amqtt coroutine to completion with `run_until_complete`, presenting the
    same synchronous `.connect()` / `.publish()` surface paho-mqtt provided.

    Any exception raised by amqtt (e.g. a connection failure or a publish
    error) propagates unchanged out of `run_until_complete` -- we do not
    catch and discard it -- matching paho-mqtt's behaviour of raising on the
    equivalent failures.
    """

    def __init__(self, cfg: BridgeConfig):
        import amqtt.client as amqtt_client  # lazy import for testability

        self._loop = asyncio.new_event_loop()
        self._mqtt_client = amqtt_client.MQTTClient(client_id=cfg.client_id)
        self._uri = "mqtt://{}:{}/".format(cfg.host, cfg.port)

    def connect(self):
        self._loop.run_until_complete(self._mqtt_client.connect(self._uri))

    def publish(self, topic, payload, qos=0):
        self._loop.run_until_complete(
            self._mqtt_client.publish(topic, payload, qos=qos)
        )
        return _PublishResult(0)


def _default_client(cfg: BridgeConfig):
    client = _amqtt_client_wrapper(cfg)
    client.connect()
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
