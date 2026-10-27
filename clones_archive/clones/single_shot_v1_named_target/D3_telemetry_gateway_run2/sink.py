"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.

Since the public API of `MqttSink.publish` is synchronous (`tag, record -> int`), we
provide a small adapter that drives an underlying `aiomqtt.Client` (async) from
synchronous code by running a private event loop. If a client is injected (e.g. in
tests), it is used as-is via a uniform `publish(topic, payload) -> rc`-like interface.
"""
import asyncio
import json


class _AiomqttSyncAdapter:
    """Adapts an async `aiomqtt.Client` to the synchronous publish(topic, payload) API."""

    def __init__(self, host: str = "localhost", port: int = 1883):
        self.host = host
        self.port = port
        self._loop = asyncio.new_event_loop()

    async def _do_publish(self, topic: str, payload: bytes):
        import aiomqtt

        async with aiomqtt.Client(hostname=self.host, port=self.port) as client:
            await client.publish(topic, payload=payload)
        return 0

    def publish(self, topic: str, payload: bytes):
        rc = self._loop.run_until_complete(self._do_publish(topic, payload))
        return _PublishResult(rc)

    def __del__(self):
        try:
            if not self._loop.is_closed():
                self._loop.close()
        except Exception:
            pass


class _PublishResult:
    def __init__(self, rc: int):
        self.rc = rc


def _default_client(host: str = "localhost", port: int = 1883):
    return _AiomqttSyncAdapter(host, port)


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        full_topic = "{}/{}".format(self.topic_base, tag)
        info = self.client.publish(full_topic, payload)
        return getattr(info, "rc", 0)
