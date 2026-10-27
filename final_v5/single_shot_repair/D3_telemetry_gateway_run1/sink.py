# CHOSEN-TARGET: gmqtt
"""sink -- MQTT publication of estimated telemetry records.

Uses `gmqtt` as the underlying MQTT client library. Since gmqtt is an
asyncio-native client, this module runs a dedicated background event loop
(in its own thread) that keeps the gmqtt client connected and serviced for
the lifetime of the sink, and marshals `publish()` calls onto that loop via
`run_coroutine_threadsafe`. This gives the rest of the codebase (and its
tests) a plain, blocking `publish()` method, matching the behaviour of the
original paho-based network-thread model.
"""
import asyncio
import json
import threading


def _default_client(host: str = "localhost", port: int = 1883):
    import gmqtt  # lazy import for testability

    client_id = f"gateway-{id(object())}"
    gm_client = gmqtt.Client(client_id)

    return _GmqttSyncAdapter(gm_client, host, port)


class _GmqttSyncAdapter:
    """Adapts an async gmqtt.Client to a synchronous publish() interface.

    Runs a background event loop thread for the whole lifetime of the
    connection so that the client's internal transport/keepalive machinery
    stays serviced, then marshals each publish() call onto that loop.
    """

    def __init__(self, gm_client, host: str, port: int):
        self._client = gm_client
        self._loop = asyncio.new_event_loop()
        self._connected = threading.Event()

        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        fut = asyncio.run_coroutine_threadsafe(
            self._client.connect(host, port), self._loop
        )
        fut.result()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def publish(self, topic: str, payload: bytes):
        fut = asyncio.run_coroutine_threadsafe(
            self._async_publish(topic, payload), self._loop
        )
        fut.result()
        return _PublishResult(rc=0)

    async def _async_publish(self, topic: str, payload: bytes):
        self._client.publish(topic, payload)

    def __del__(self):
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass


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
