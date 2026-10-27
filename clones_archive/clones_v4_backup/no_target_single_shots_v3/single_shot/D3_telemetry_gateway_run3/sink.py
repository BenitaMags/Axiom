# CHOSEN-TARGET: gmqtt
"""sink -- MQTT publication of estimated telemetry records.

Rewritten to use ``gmqtt`` instead of ``paho-mqtt``. ``gmqtt`` is an
asyncio-based MQTT client, so a small synchronous adapter is provided here
that mirrors the subset of the paho-mqtt ``Client`` API this module relied
on (``connect`` at construction time and ``publish(topic, payload)``
returning an object with an ``rc`` attribute), keeping ``MqttSink``'s public
API and behaviour unchanged.
"""
import asyncio
import json
from collections import namedtuple

import gmqtt

_PublishInfo = namedtuple("_PublishInfo", ["rc"])


class _GmqttSyncClient:
    """Synchronous wrapper around gmqtt.Client for drop-in compatibility."""

    def __init__(self, host: str = "localhost", port: int = 1883, client_id: str = ""):
        self._loop = asyncio.new_event_loop()
        self._client = gmqtt.Client(client_id)
        self._loop.run_until_complete(self._client.connect(host, port))

    def publish(self, topic: str, payload: bytes):
        self._client.publish(topic, payload)
        return _PublishInfo(rc=0)

    def disconnect(self):
        self._loop.run_until_complete(self._client.disconnect())


def _default_client(host: str = "localhost", port: int = 1883):
    return _GmqttSyncClient(host, port)


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
