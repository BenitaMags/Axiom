"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import asyncio
import json


def _axiom_amqtt_client_wrapper(host: str = "localhost", port: int = 1883):
    """Bridge amqtt's async MQTTClient to the synchronous connect()/publish()
    interface that paho-mqtt exposed.

    paho-mqtt's Client.connect()/publish() are synchronous, direct method
    calls, and publish() returns an MQTTMessageInfo object with an integer
    'rc' attribute (0 on success). amqtt.MQTTClient instead exposes
    async connect()/publish() coroutines and signals failure by raising an
    exception rather than returning an 'rc' code.

    This wrapper builds an amqtt.MQTTClient, connects it immediately, and
    returns a small synchronous adapter object whose connect()/publish()
    methods drive the underlying coroutines to completion on a dedicated
    event loop (one run_until_complete() call per invocation) and translate
    amqtt's exception-based failure signaling into a paho-mqtt-style 'rc'
    attribute (0 for success, 1 for failure). This lets existing
    synchronous call sites keep calling connect()/publish() unchanged.
    """
    import _Result  # connector: bound to wrapper scope
    from amqtt.client import MQTTClient

    class _SyncAmqttAdapter:
        def __init__(self, uri: str):
            self._uri = uri
            self._client = MQTTClient()
            self._loop = asyncio.new_event_loop()
            self._loop.run_until_complete(self._client.connect(self._uri))

        def connect(self, host: str, port: int):
            # Connection is already established at construction time; this
            # method is kept only for API compatibility with paho-mqtt's
            # Client.connect(host, port) signature.
            return 0

        def publish(self, topic: str, payload: bytes):
            class _Result:
                rc = 0

            try:
                self._loop.run_until_complete(
                    self._client.publish(topic, payload)
                )
                _Result.rc = 0
            except Exception:
                _Result.rc = 1
            _spt_r1 = _Result()
            return _spt_r1
    _spt_r2 = _SyncAmqttAdapter(f"mqtt://{host}:{port}")
    return _spt_r2


def _default_client(host: str = "localhost", port: int = 1883):
    client = _axiom_amqtt_client_wrapper(host, port)
    client.connect(host, port)
    return client


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        if not (client is not None):
            self.client = _default_client()
        else:
            self.client = client

    def publish(self, tag: str, record: dict) -> int:
        _spt_r3 = getattr(self.client.publish(f"{self.topic_base}/{tag}", json.dumps(record, separators=(",", ":")).encode("utf-8")), "rc", 0)
        return _spt_r3
