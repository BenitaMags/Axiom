"""Support classes for bridging amqtt's async client to a synchronous,
paho-mqtt-shaped interface.

Extracted from bridge.py so the glue logic between amqtt's coroutine-based
API and paho-mqtt's blocking, rc-returning API lives in one focused place.
"""
import asyncio
import threading

from mqtt_config import BridgeConfig


class PublishResult:
    """Minimal stand-in for paho-mqtt's MQTTMessageInfo.

    paho-mqtt's ``Client.publish()`` returns an ``MQTTMessageInfo`` whose
    ``rc`` attribute is ``MQTT_ERR_SUCCESS`` (0) on success or a nonzero
    paho error code (e.g. ``MQTT_ERR_NO_CONN``) when the publish failed.
    amqtt's ``MQTTClient.publish()`` coroutine has no return value at all on
    success and instead *raises* when the publish/connection fails. This
    object reproduces paho's ``rc``-bearing contract on top of that: rc=0
    when the coroutine completed without raising, rc=1 when amqtt raised one
    of the failure classes paho reported as a nonzero rc for the same
    condition (connection lost, refused, or timed out).
    """

    def __init__(self, rc):
        self.rc = rc


class SyncAmqttClient:
    """Synchronous facade around amqtt's async MQTTClient.

    paho-mqtt's Client is fully synchronous: connect() blocks until the
    handshake completes and publish() is a plain, immediately-returning
    call. amqtt only exposes coroutines. This facade starts a single
    persistent background asyncio event loop (NOT asyncio.run per call --
    that would tear the connection's transport down between calls) and
    marshals connect()/publish()/disconnect() onto it, blocking the caller
    thread for each result the way paho-mqtt's calls used to block.
    """

    def __init__(self, cfg: BridgeConfig):
        import amqtt.client  # lazy import for testability, mirrors original

        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()
        self._client = amqtt.client.MQTTClient(client_id=cfg.client_id)

    def _run(self, coro):
        """Run a coroutine on the persistent loop and block for its result,
        re-raising whatever exception it raised (mirrors a blocking call)."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def connect(self, host, port):
        uri = f"mqtt://{host}:{port}"
        self._run(self._client.connect(uri))

    def publish(self, topic, payload, qos=0):
        """Publish and translate amqtt's contract back to paho-mqtt's.

        amqtt's publish() coroutine returns None on success -- it awaits the
        QoS 1/2 handshake internally but never surfaces an ack object -- and
        raises on connection/protocol failure or timeout. paho-mqtt reported
        that same class of failure via a nonzero `rc` on the returned
        MQTTMessageInfo instead of raising. We catch exactly the failure
        modes paho-mqtt turned into a nonzero rc (connection/protocol errors,
        refused/lost connections, timeouts) and convert them to rc=1, paho's
        generic non-success code. Anything else (e.g. a bad-argument
        TypeError) propagates, since paho let unrelated errors escape too.
        On success we return rc=0; for QoS 0 that only means "handed to the
        transport", not "acknowledged", which is the same guarantee amqtt
        itself makes.
        """
        from amqtt.client import ClientException, ConnectException
        from amqtt.errors import MQTTException

        try:
            self._run(self._client.publish(topic, payload, qos=qos))
        except (ClientException, ConnectException, MQTTException,
                asyncio.TimeoutError, OSError):
            return PublishResult(1)
        return PublishResult(0)

    def close(self):
        self._run(self._client.disconnect())
        self._loop.call_soon_threadsafe(self._loop.stop)
