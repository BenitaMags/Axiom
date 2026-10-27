# CHOSEN-TARGET: gmqtt
import json
import asyncio


def _default_client(host: str = "localhost", port: int = 1883):
    """Lazily build a connected gmqtt client wrapped in a sync-friendly adapter."""
    from gmqtt import Client as GmqttClient

    async def _connect():
        c = GmqttClient(client_id=None)
        await c.connect(host, port)
        return c

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    raw_client = loop.run_until_complete(_connect())
    return _GmqttSyncAdapter(raw_client, loop)


class _GmqttSyncAdapter:
    """Adapts gmqtt's async publish API to a simple synchronous `.publish()` call.

    gmqtt has no notion of a per-call return code (unlike paho), so this adapter
    always reports success (0) once the publish coroutine has been scheduled/run,
    matching the previous best-effort semantics used by MqttSink.
    """

    def __init__(self, raw_client, loop):
        self._raw = raw_client
        self._loop = loop

    def publish(self, topic: str, payload: bytes):
        result = self._raw.publish(topic, payload)
        if asyncio.iscoroutine(result):
            self._loop.run_until_complete(result)
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
