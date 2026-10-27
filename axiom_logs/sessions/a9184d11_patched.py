"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import json
import asyncio
import threading


def _amqtt_sync_bridge_client(host: str = "localhost", port: int = 1883):
    """Build a synchronous facade over amqtt's async MQTTClient.

    paho-mqtt's Client.connect()/Client.publish() are synchronous and
    Client.publish() hands back a MQTTMessageInfo whose ``.rc`` is 0 on
    success and nonzero on failure. amqtt.client.MQTTClient is asyncio-only:
    ``connect()`` is a coroutine that returns the CONNACK return code
    (0 == accepted, nonzero == rejected by the broker) and ``publish()`` is a
    coroutine that returns ``None`` -- amqtt gives no delivery confirmation
    at QoS 0 (same as paho at QoS 0) and instead raises on transport/protocol
    failure (refused connection, timeout, malformed packet, ...) rather than
    returning a status code.

    To keep the connection bound to a single live event loop across calls
    (rather than opening/closing a loop -- and the connection with it -- on
    every call), this facade starts ONE persistent background event loop and
    runs every amqtt coroutine on it via
    ``asyncio.run_coroutine_threadsafe(...).result()``, which blocks the
    calling thread and re-raises whatever the coroutine raised.

    ``.connect(host, port)`` returns an object with ``.rc`` set to the
    CONNACK code amqtt reported (0 on success), or to 1 if amqtt raised a
    connection/transport exception -- mirroring paho's use of a nonzero rc
    for "the operation failed".

    ``.publish(topic, payload)`` returns an object with ``.rc`` = 0 once
    amqtt's publish coroutine completes without raising. This only means the
    message was handed to amqtt for sending/queuing -- neither amqtt nor
    paho at QoS 0 waits for a broker acknowledgement -- so it must not be
    read as "delivered". If amqtt raises, ``.rc`` = 1 is returned instead of
    swallowing the failure, so callers that check ``.rc`` still observe it.
    """
    from amqtt.client import MQTTClient  # lazy import for testability
    from amqtt.errors import MQTTError  # lazy import for testability

    class _Result:
        def __init__(self, rc):
            self.rc = rc

    class _SyncFacade:
        def __init__(self):
            self._loop = asyncio.new_event_loop()
            threading.Thread(target=self._loop.run_forever, daemon=True).start()
            self._client = MQTTClient()

        def _run(self, coro):
            return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

        def connect(self, host, port):
            uri = f"mqtt://{host}:{port}/"
            try:
                rc = self._run(self._client.connect(uri))
            except (MQTTError, OSError, ConnectionError, asyncio.TimeoutError):
                return _Result(1)
            return _Result(rc if rc is not None else 0)

        def publish(self, topic, payload):
            try:
                self._run(self._client.publish(topic, payload))
            except (MQTTError, OSError, ConnectionError, asyncio.TimeoutError):
                return _Result(1)
            return _Result(0)

        def close(self):
            try:
                self._run(self._client.disconnect())
            except (MQTTError, OSError, ConnectionError, asyncio.TimeoutError):
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)

    return _SyncFacade()


def _default_client(host: str = "localhost", port: int = 1883):
    client = _amqtt_sync_bridge_client(host, port)
    result = client.connect(host, port)
    if result.rc != 0:
        raise ConnectionError(f"amqtt connect failed with rc={result.rc}")
    return client


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
