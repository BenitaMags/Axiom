"""Helpers bridging amqtt's async-only client to a synchronous facade.

Extracted from bridge.py so the async/threading glue lives in one place.
"""


class _PublishResult:
    """Mimics paho-mqtt's MessageInfo, which exposed an ``rc`` attribute
    (0 == MQTT_ERR_SUCCESS) after a publish call. amqtt's MQTTClient.publish
    coroutine does not return an rc code at all -- for qos 0 it merely queues
    the message, and for qos >= 1 it returns once the ack handshake for that
    QoS level completes, but with no return value to inspect. Since awaiting
    the coroutine without an exception is the only signal amqtt gives us,
    we report success (rc=0) once it returns; any failure instead surfaces
    as a raised exception (see _SyncFacade.publish), so callers who only
    check ``rc`` won't be silently lied to on failure paths.
    """

    def __init__(self, rc: int = 0):
        self.rc = rc


class _SyncFacade:
    """Bridges amqtt's async-only MQTTClient to the synchronous connect()/
    publish() calls this module's callers expect from paho-mqtt.

    A persistent background event loop is started once and kept alive for
    the lifetime of this object, and every operation is submitted to it via
    asyncio.run_coroutine_threadsafe(...).result(). This is deliberate: using
    asyncio.run() per call would tear the loop (and the connection bound to
    it) down after every single coroutine, so the next call would operate on
    a dead transport. Keeping one loop running keeps the underlying amqtt
    connection valid across repeated publish() calls.
    """

    def __init__(self, client_id: str, host: str, port: int):
        import asyncio
        import threading

        self._asyncio = asyncio
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()

        from amqtt.client import MQTTClient

        self._client = MQTTClient(client_id=client_id)
        uri = "mqtt://{}:{}".format(host, port)
        self._run(self._client.connect(uri))

    def _run(self, coro):
        return self._asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def publish(self, topic, payload, qos=0):
        """Publish and return a _PublishResult mimicking paho-mqtt's
        MessageInfo.rc. Exceptions from amqtt (connection errors, protocol
        errors) are allowed to propagate, matching the fact that paho-mqtt's
        publish() itself could raise (e.g. ValueError on a bad topic) rather
        than encode every failure as an rc code.
        """
        self._run(self._client.publish(topic, payload, qos=qos))
        return _PublishResult(0)

    def close(self):
        self._run(self._client.disconnect())
        self._loop.call_soon_threadsafe(self._loop.stop)
