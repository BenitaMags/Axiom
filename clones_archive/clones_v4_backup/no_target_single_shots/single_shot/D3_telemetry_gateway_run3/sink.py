# CHOSEN-TARGET: gmqtt
"""sink -- MQTT publication of estimated telemetry records.

Uses gmqtt as the underlying MQTT implementation. Since gmqtt is asyncio-based
and paho-mqtt's Client.publish() is synchronous, a thin adapter is provided
that spins up (and reuses) a background event loop to bridge the async API to
the synchronous `publish` interface expected by callers/tests.
"""
import asyncio
import json
import threading


class _AsyncLoopRunner:
    """Owns a background thread running a private asyncio event loop."""

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        self._ready.wait()

    def _spin(self):
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    def run(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()


class _GmqttClientAdapter:
    """Wraps a gmqtt.Client to expose paho-like connect()/publish() calls."""

    def __init__(self, host: str, port: int, client_id: str = "estimator-sink"):
        import gmqtt  # lazy import for testability

        self._gmqtt = gmqtt
        self._runner = _AsyncLoopRunner()
        self._client = gmqtt.Client(client_id)
        self._runner.run(self._client.connect(host, port))

    def publish(self, topic: str, payload: bytes):
        self._client.publish(topic, payload)
        return _PublishInfo(rc=0)


class _PublishInfo:
    def __init__(self, rc: int = 0):
        self.rc = rc


def _default_client(host: str = "localhost", port: int = 1883):
    return _GmqttClientAdapter(host, port)


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
