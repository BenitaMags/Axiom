"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import json

from amqtt_bridge import _amqtt_client_wrapper, _AmqttSyncClient, _PublishResult


def _default_client(host: str = "localhost", port: int = 1883):
    return _amqtt_client_wrapper(host, port)


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def _encode(self, record: dict) -> bytes:
        return json.dumps(record, separators=(",", ":")).encode("utf-8")

    def _topic_for(self, tag: str) -> str:
        return f"{self.topic_base}/{tag}"

    def publish(self, tag: str, record: dict) -> int:
        payload = self._encode(record)
        info = self.client.publish(self._topic_for(tag), payload)
        return getattr(info, "rc", 0)
