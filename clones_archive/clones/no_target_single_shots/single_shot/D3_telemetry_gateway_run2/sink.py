# CHOSEN-TARGET: gmqtt
"""sink -- MQTT publication of estimated telemetry records.

Uses `gmqtt` as the underlying MQTT implementation instead of `paho-mqtt`.
Since gmqtt is asyncio-native, a thin synchronous adapter is provided so the
public, synchronous `MqttSink` API stays identical to before.
"""
import asyncio
import json


class _GmqttSyncAdapter:
    """Wraps an asyncio-based gmqtt client behind a synchronous facade.

    Provides just enough surface (`publish`) for MqttSink to remain
    fully synchronous from the caller's point of view.
    """

    def __init__(self, host: str, port: int):
        import gmqtt  # lazy import for testability

        self._gmqtt_module = gmqtt
        self._host = host
        self._port = port
        self._loop = asyncio.new_event_loop()
        self._client = gmqtt.Client(client_id=f"gateway-{id(self)}")
        self._connected = False
        self._connect_if_needed()

    def _connect_if_needed(self):
        if self._connected:
            return
        self._loop.run_until_complete(
            self._client.connect(self._host, self._port)
        )
        self._connected = True

    def publish(self, topic: str, payload: bytes):
        self._connect_if_needed()
        self._client.publish(topic, payload)
        return _PublishResult(rc=0)


class _PublishResult:
    __slots__ = ("rc",)

    def __init__(self, rc: int):
        self.rc = rc


def _default_client(host: str = "localhost", port: int = 1883):
    return _GmqttSyncAdapter(host, port)


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        encoded = json.dumps(record, separators=(",", ":")).encode("utf-8")
        destination = f"{self.topic_base}/{tag}"
        info = self.client.publish(destination, encoded)
        return getattr(info, "rc", 0)
