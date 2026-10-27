# CHOSEN-TARGET: gmqtt
"""sink -- MQTT publication of estimated telemetry records.

Uses `gmqtt` as the underlying MQTT implementation instead of `paho-mqtt`.
Since gmqtt is asyncio-native, a small synchronous adapter is provided so the
public `MqttSink` API remains synchronous and drop-in compatible.
"""
import asyncio
import json


class _SyncGmqttAdapter:
    """Thin synchronous wrapper around a gmqtt.Client instance.

    Provides a `publish(topic, payload)` method returning an object with an
    `.rc` attribute, mirroring the shape historically returned by paho's
    `client.publish(...)` call (a `MQTTMessageInfo`-like object).
    """

    class _PublishResult:
        __slots__ = ("rc",)

        def __init__(self, rc: int = 0):
            self.rc = rc

    def __init__(self, host: str, port: int):
        import gmqtt  # local import so the dependency is only needed when used

        self._gmqtt = gmqtt
        self._host = host
        self._port = port
        self._loop = asyncio.new_event_loop()
        self._client = gmqtt.Client(client_id=f"telemetry-sink-{id(self)}")
        self._connected = False
        self._connect()

    def _connect(self):
        async def _do_connect():
            await self._client.connect(self._host, self._port)

        self._loop.run_until_complete(_do_connect())
        self._connected = True

    def publish(self, topic: str, payload: bytes):
        # gmqtt's publish call is fire-and-forget / non-blocking, so we just
        # invoke it synchronously via the loop and report success (rc=0)
        # unless an exception is raised.
        try:
            self._client.publish(topic, payload)
            rc = 0
        except Exception:
            rc = 1
        return self._PublishResult(rc)


def _default_client(host: str = "localhost", port: int = 1883):
    return _SyncGmqttAdapter(host, port)


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        encoded_payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        full_topic = "{}/{}".format(self.topic_base, tag)
        info = self.client.publish(full_topic, encoded_payload)
        return getattr(info, "rc", 0)
