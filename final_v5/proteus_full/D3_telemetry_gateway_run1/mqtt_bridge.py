"""Helper module providing a synchronous facade over amqtt's async client.

Kept separate from sink.py so the sync/async bridging plumbing can be reused
or substituted independently of the sink's publishing logic. The amqtt
imports remain deferred inside the factory function for testability (the
module must remain importable even when amqtt is not installed).
"""
import asyncio
import threading


class _Result:
    def __init__(self, rc):
        self.rc = rc


class _SyncFacade:
    def __init__(self, client_cls, retry_exceptions):
        self._retry_exceptions = retry_exceptions
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()
        self._client = client_cls()

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def connect(self, host, port):
        uri = f"mqtt://{host}:{port}/"
        try:
            rc = self._run(self._client.connect(uri))
        except self._retry_exceptions:
            return _Result(1)
        return _Result(rc if rc is not None else 0)

    def publish(self, topic, payload):
        try:
            self._run(self._client.publish(topic, payload))
        except self._retry_exceptions:
            return _Result(1)
        return _Result(0)

    def close(self):
        try:
            self._run(self._client.disconnect())
        except self._retry_exceptions:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)


def build_sync_bridge_client(host: str = "localhost", port: int = 1883):
    """Build a synchronous facade over amqtt's async MQTTClient.

    See ``sink._amqtt_sync_bridge_client`` for the full rationale; this
    function performs the actual construction so the lazy amqtt import stays
    isolated to a single call site.
    """
    from amqtt.client import MQTTClient  # lazy import for testability
    from amqtt.errors import MQTTError  # lazy import for testability

    retry_exceptions = (MQTTError, OSError, ConnectionError, asyncio.TimeoutError)
    return _SyncFacade(MQTTClient, retry_exceptions)
