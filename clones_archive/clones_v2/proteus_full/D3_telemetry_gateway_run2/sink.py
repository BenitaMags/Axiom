"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import asyncio
import json


class _AxiomAmqttSyncFacade:
    """Synchronous-looking facade wrapping an amqtt.client.MQTTClient.

    paho.mqtt.client.Client exposes plain synchronous connect()/publish()/
    disconnect() methods, and publish() returns a paho MQTTMessageInfo
    object exposing an `.rc` result code attribute that callers inspect
    directly.

    amqtt.client.MQTTClient instead exposes coroutine methods (connect,
    publish, disconnect) that must be awaited inside an asyncio event loop,
    and its publish() coroutine returns None (or raises) rather than an
    object with `.rc`.

    This facade owns a private asyncio event loop and uses
    loop.run_until_complete(...) to drive the amqtt coroutines to
    completion synchronously, translating the outcome into a small result
    object exposing `.rc` (0 on success, 1 if an exception was raised) so
    existing call sites written against paho's synchronous, .rc-based API
    keep working unchanged.
    """

    class _Result:
        def __init__(self, rc: int):
            self.rc = rc

    def __init__(self, client):
        self._client = client
        self._loop = asyncio.new_event_loop()

    def connect(self, host: str = "localhost", port: int = 1883):
        uri = f"mqtt://{host}:{port}/"
        try:
            self._loop.run_until_complete(self._client.connect(uri))
            return self._Result(0)
        except Exception:
            return self._Result(1)

    def publish(self, topic: str, payload: bytes):
        try:
            self._loop.run_until_complete(self._client.publish(topic, payload))
            return self._Result(0)
        except Exception:
            return self._Result(1)

    def disconnect(self):
        try:
            self._loop.run_until_complete(self._client.disconnect())
            return self._Result(0)
        except Exception:
            return self._Result(1)
        finally:
            self._loop.close()


def _axiom_mqtt_client_factory_wrapper():
    """Build a paho-like synchronous MQTT client backed by amqtt.

    paho.mqtt.client.Client() returns a synchronous client object whose
    connect() and publish() calls block and return immediately-usable
    results (publish() returns an MQTTMessageInfo with an `.rc` code).

    amqtt.client's MQTTClient is asyncio-native: connect()/publish()/
    disconnect() are coroutines that must be awaited on an event loop, and
    it has no equivalent of paho's MQTTMessageInfo.rc.

    This factory constructs an amqtt.client.MQTTClient and wraps it in a
    _AxiomAmqttSyncFacade, which internally drives the coroutines to
    completion via asyncio.run_until_complete and exposes plain
    connect()/publish()/disconnect() methods returning `.rc`-bearing
    results, so existing call sites written against paho's synchronous API
    continue to work unchanged.
    """
    from amqtt.client import MQTTClient

    client = MQTTClient()
    return _AxiomAmqttSyncFacade(client)


def _default_client(host: str = "localhost", port: int = 1883):
    client = _axiom_mqtt_client_factory_wrapper()
    client.connect(host, port)
    return client


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
