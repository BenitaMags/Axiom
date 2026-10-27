# CHOSEN-TARGET: gmqtt
"""sink -- MQTT publication of estimated telemetry records.



"""
import asyncio
import json

import gmqtt


class _SyncGmqttClient:
    """Thin synchronous wrapper around gmqtt.Client providing a paho-like API."""

    def __init__(self, host: str = "localhost", port: int = 1883):
        self._host = host
        self._port = port
        self._loop = asyncio.new_event_loop()
        self._client = gmqtt.Client(client_id="")
        self._loop.run_until_complete(self._client.connect(host, port))

    def publish(self, topic: str, payload: bytes):
        self._client.publish(topic, payload)
        return _PublishInfo(rc=0)


class _PublishInfo:
    def __init__(self, rc: int = 0):
        self.rc = rc


def _default_client(host: str = "localhost", port: int = 1883):
    return _SyncGmqttClient(host, port)


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
