"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.

Since `aiomqtt` is async-only and context-manager based, this module provides a small
synchronous adapter (`_AiomqttSyncAdapter`) that owns a background event loop thread,
enters the `aiomqtt.Client` async context once, and exposes a synchronous `.publish(...)`
method so the rest of the (synchronous) codebase and its public API are unaffected.
"""
import asyncio
import json
import threading


class _AiomqttSyncAdapter:
    """Wraps an aiomqtt.Client so it can be driven from synchronous code.

    Runs a private asyncio event loop on a background thread, keeps the
    aiomqtt client's async context manager open for the adapter's lifetime,
    and marshals `.publish()` calls onto that loop.
    """

    def __init__(self, host: str = "localhost", port: int = 1883):
        import aiomqtt  # lazy import for testability

        self._aiomqtt = aiomqtt
        self._host = host
        self._port = port

        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._ready.wait()

        self._client_cm = None
        self._client = None
        fut = asyncio.run_coroutine_threadsafe(self._enter_client(), self._loop)
        fut.result()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    async def _enter_client(self):
        self._client_cm = self._aiomqtt.Client(self._host, self._port)
        self._client = await self._client_cm.__aenter__()

    async def _do_publish(self, topic: str, payload: bytes):
        await self._client.publish(topic, payload)
        return 0

    def publish(self, topic: str, payload: bytes) -> int:
        fut = asyncio.run_coroutine_threadsafe(
            self._do_publish(topic, payload), self._loop
        )
        return fut.result()

    def __del__(self):
        try:
            if self._client_cm is not None:
                fut = asyncio.run_coroutine_threadsafe(
                    self._client_cm.__aexit__(None, None, None), self._loop
                )
                fut.result(timeout=5)
        except Exception:
            pass
        finally:
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass


def _default_client(host: str = "localhost", port: int = 1883):
    return _AiomqttSyncAdapter(host, port)


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        encoded = json.dumps(record, separators=(",", ":")).encode("utf-8")
        topic = self.topic_base + "/" + tag
        result = self.client.publish(topic, encoded)
        rc = getattr(result, "rc", None)
        return rc if rc is not None else (result if isinstance(result, int) else 0)
