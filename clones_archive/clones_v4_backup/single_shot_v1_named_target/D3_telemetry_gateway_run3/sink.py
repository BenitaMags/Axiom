"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.

Since `aiomqtt` is async-only and exposes no synchronous publish API, this module
provides a small adapter (`_AsyncClientBridge`) that owns a background event loop and
an open `aiomqtt.Client` connection, exposing a synchronous `publish(topic, payload)`
method compatible with the previous paho-mqtt-based call site in `MqttSink.publish`.
"""
import asyncio
import json
import threading


class _AsyncClientBridge:
    """Adapts an async aiomqtt.Client to a synchronous publish() call.

    Runs a dedicated background thread with its own event loop, inside which the
    aiomqtt connection is opened once and kept alive for the object's lifetime.
    """

    def __init__(self, host: str = "localhost", port: int = 1883):
        self._host = host
        self._port = port
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._client = None
        self._client_cm = None
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._ready.wait()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._open_connection())
        self._ready.set()
        self._loop.run_forever()

    async def _open_connection(self):
        import aiomqtt

        self._client_cm = aiomqtt.Client(hostname=self._host, port=self._port)
        self._client = await self._client_cm.__aenter__()

    async def _do_publish(self, topic: str, payload: bytes):
        await self._client.publish(topic, payload=payload)
        return 0

    def publish(self, topic: str, payload: bytes) -> int:
        future = asyncio.run_coroutine_threadsafe(
            self._do_publish(topic, payload), self._loop
        )
        return future.result()

    def close(self):
        async def _shutdown():
            if self._client_cm is not None:
                await self._client_cm.__aexit__(None, None, None)

        try:
            fut = asyncio.run_coroutine_threadsafe(_shutdown(), self._loop)
            fut.result(timeout=5)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)


def _default_client(host: str = "localhost", port: int = 1883):
    return _AsyncClientBridge(host=host, port=port)


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        encoded = json.dumps(record, separators=(",", ":")).encode("utf-8")
        topic = "{}/{}".format(self.topic_base, tag)
        result = self.client.publish(topic, encoded)
        return getattr(result, "rc", result if isinstance(result, int) else 0)
