# CHOSEN-TARGET: gmqtt
"""sink -- MQTT publication of estimated telemetry records.

Uses `gmqtt` under an adapter so that the surrounding gateway/estimator code
does not need to know about gmqtt's asyncio-based API. A thin synchronous
wrapper (`_GmqttSyncClient`) exposes a `publish(topic, payload)` method with
a `.rc` style return, mirroring how the previous MQTT client was used.
"""
import asyncio
import json

RC_SUCCESS = 0
RC_FAILURE = 1


class _PublishResult:
    """Small result object mimicking the old `.rc` attribute access."""

    def __init__(self, rc: int):
        self.rc = rc


class _GmqttSyncClient:
    """Synchronous adapter around gmqtt's asyncio Client.

    Manages its own event loop so callers can invoke `publish()` and
    `connect()` as plain blocking calls, exactly like the previous
    paho-mqtt based client usage in this module.

    gmqtt's `Client` relies internally on `asyncio.get_event_loop()` at
    construction and connection time, so the private loop created here is
    installed as the *current* event loop for this thread before the
    client object is built and before any coroutines are run on it.
    """

    def __init__(self, client_id: str = "telemetry-gateway"):
        import gmqtt  # lazy import for testability

        self._gmqtt = gmqtt
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._client = gmqtt.Client(client_id)
        self._connected = False

    def connect(self, host: str = "localhost", port: int = 1883):
        asyncio.set_event_loop(self._loop)

        async def _do_connect():
            await self._client.connect(host, port)

        self._loop.run_until_complete(_do_connect())
        self._connected = True

    def publish(self, topic: str, payload: bytes) -> _PublishResult:
        try:
            asyncio.set_event_loop(self._loop)
            self._client.publish(topic, payload)
            return _PublishResult(RC_SUCCESS)
        except Exception:
            return _PublishResult(RC_FAILURE)

    def disconnect(self):
        if self._connected:
            asyncio.set_event_loop(self._loop)

            async def _do_disconnect():
                await self._client.disconnect()

            self._loop.run_until_complete(_do_disconnect())
            self._connected = False


def _default_client(host: str = "localhost", port: int = 1883):
    client = _GmqttSyncClient()
    client.connect(host, port)
    return client


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
