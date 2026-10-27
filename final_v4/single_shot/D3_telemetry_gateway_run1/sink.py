# CHOSEN-TARGET: gmqtt
"""sink -- MQTT publication of estimated telemetry records.

Uses `gmqtt` as the underlying MQTT client library. Since gmqtt is an
asyncio-native client, this module wraps the async publish call behind a
small synchronous adapter so the rest of the codebase (and its tests) can
keep calling `publish()` as a plain, blocking method.
"""
import asyncio
import json


def _default_client(host: str = "localhost", port: int = 1883):
    import gmqtt  # lazy import for testability

    client_id = f"gateway-{id(object())}"
    gm_client = gmqtt.Client(client_id)

    loop = asyncio.new_event_loop()
    loop.run_until_complete(gm_client.connect(host, port))

    return _GmqttSyncAdapter(gm_client, loop)


class _GmqttSyncAdapter:
    """Adapts an async gmqtt.Client to a synchronous publish() interface."""

    def __init__(self, gm_client, loop):
        self._client = gm_client
        self._loop = loop

    def publish(self, topic: str, payload: bytes):
        self._client.publish(topic, payload)
        return _PublishResult(rc=0)


class _PublishResult:
    def __init__(self, rc: int = 0):
        self.rc = rc


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
