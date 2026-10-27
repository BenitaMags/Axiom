"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for

import asyncio

from amqtt.client import MQTTClient


class _SyncAmqttClientWrapper:
    """Synchronous facade over amqtt.client.MQTTClient.

    paho-mqtt's mqtt.Client exposes blocking `.connect(host, port)` and
    `.publish(topic, payload, qos=...)` methods, where `.publish()` returns
    an MQTTMessageInfo object carrying an `.rc` status code. amqtt's
    MQTTClient instead exposes async coroutines `connect(uri)`, `publish()`
    and `disconnect()` that must be awaited inside an asyncio event loop,
    and `publish()` resolves to None (or raises) instead of returning an
    object with an `.rc` field.

    This wrapper keeps a private, persistent event loop and drives the
    amqtt coroutines via `loop.run_until_complete(...)`, exposing the same
    blocking `.connect(host, port)` / `.publish(topic, payload, qos=...)`
    surface the rest of this module was written against. Since amqtt does
    not provide an equivalent result code, `rc` is synthesized as 0 when
    the publish coroutine completes without raising, and 1 if it raises.
    """

    def __init__(self, client_id: str):
        self._loop = asyncio.new_event_loop()
        self._client = MQTTClient(client_id=client_id, loop=self._loop)

    def connect(self, host: str, port: int):
        uri = f"mqtt://{host}:{port}"
        return self._loop.run_until_complete(self._client.connect(uri))

    def publish(self, topic: str, payload, qos: int = 0):
        class _Result:
            pass

        result = _Result()
        try:
            self._loop.run_until_complete(
                self._client.publish(topic, payload, qos=qos)
            )
            result.rc = 0
        except Exception:
            result.rc = 1
        return result

    def disconnect(self):
        return self._loop.run_until_complete(self._client.disconnect())


def _axiom_mqtt_client_wrapper(cfg: BridgeConfig):
    """Build a synchronous-looking MQTT client backed by amqtt.

    Previously `_default_client` constructed a `paho.mqtt.client.Client`,
    called its blocking `.connect(host, port)`, and returned it so callers
    could invoke `.publish(topic, payload, qos=...)` synchronously and read
    `.rc` off the result. amqtt only exposes an async client, so this
    wrapper instantiates a `_SyncAmqttClientWrapper` (see its docstring for
    the connect/publish/rc translation) and performs the initial connect,
    letting the rest of TelemetryBridge stay synchronous and unchanged.
    """
    client = _SyncAmqttClientWrapper(cfg.client_id)
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
