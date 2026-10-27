"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `gmqtt` (asyncio-based). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
import asyncio
import threading

from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _PublishResult:
    """Minimal stand-in for paho-mqtt's MQTTMessageInfo.

    Call sites read `.rc` off whatever `publish()` returns. gmqtt's
    `Client.publish()` is fire-and-forget: it queues/sends the packet and
    returns None, it does not hand back a status code, and for QoS 0 there
    is no broker acknowledgement to wait on at all. We report rc=0 only to
    mean "the library accepted and queued the message", mirroring paho's
    MQTT_ERR_SUCCESS for that same case -- not "the broker confirmed
    delivery", which gmqtt's publish() never tells us for QoS 0.
    """

    def __init__(self, rc: int):
        self.rc = rc


def _gmqtt_threadsafe_publish_wrapper(client, loop, topic, payload, qos: int = 0):
    """Publish through gmqtt from the *event loop's* thread, not the caller's.

    paho-mqtt's `Client.publish()` is safe to call from any thread and
    returns an `MQTTMessageInfo` whose `.rc` is `MQTT_ERR_SUCCESS` (0) when
    the packet was queued, or `MQTT_ERR_NO_CONN` (4) -- without raising --
    when the client is not currently connected to a broker; the message is
    just dropped in that case. gmqtt's `Client.publish()` touches asyncio
    transport/state that belongs to the background event loop, so it must
    not be invoked directly from the caller's thread; instead we marshal it
    onto the loop via `run_coroutine_threadsafe(...).result()`, which blocks
    the caller until gmqtt has actually processed the call (not merely
    scheduled it) and re-raises whatever gmqtt raised.

    gmqtt itself returns nothing from `publish()` -- it does not hand back a
    status code, so on success there is nothing to "use"; we report rc=0 to
    mean "the library accepted and queued the message" (paho's
    MQTT_ERR_SUCCESS for that same case), not "the broker confirmed
    delivery". The one failure gmqtt reports for the same condition paho
    reports MQTT_ERR_NO_CONN for -- no live connection to publish on -- it
    reports by raising (its transport is `None` or closed), so we catch
    exactly that family of transport failures and translate it into the
    same MQTT_ERR_NO_CONN status paho used instead of letting an unfamiliar
    exception type reach the caller. Any other exception (e.g. a bad
    topic/qos, which paho itself raised as ValueError) is a genuine
    programming error and is left to propagate unchanged.
    """
    MQTT_ERR_NO_CONN = 4

    async def _publish_coro():
        client.publish(topic, payload, qos=qos)

    future = asyncio.run_coroutine_threadsafe(_publish_coro(), loop)
    try:
        future.result()
    except (ConnectionError, OSError, AttributeError):
        return _PublishResult(MQTT_ERR_NO_CONN)
    return _PublishResult(0)


class _SyncMQTTClient:
    """Synchronous facade around gmqtt's asyncio-only Client.

    paho-mqtt's `Client.connect()` / `Client.publish()` are ordinary
    blocking calls. gmqtt's equivalents are coroutines that must run on an
    asyncio event loop. To let synchronous callers keep calling `.connect`-
    then-`.publish` in the old style, this facade starts one persistent
    background event loop (not a fresh `asyncio.run()` per call -- that
    would tear the loop, and the connection bound to it, down after every
    single call) and marshals each coroutine onto that loop with
    `run_coroutine_threadsafe(...).result()`, which blocks the calling
    thread until the coroutine finishes and re-raises any exception it
    raised so failures still reach the caller.
    """

    def __init__(self, client_id: str, host: str, port: int):
        import gmqtt  # lazy import for testability

        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()
        self._client = gmqtt.Client(client_id)
        self._run(self._client.connect(host, port))

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def publish(self, topic, payload, qos: int = 0):
        # gmqtt touches asyncio transport state that belongs to the
        # background loop, so the call is marshaled onto that loop (see
        # `_gmqtt_threadsafe_publish_wrapper`) instead of being invoked
        # directly from whatever thread this method happens to run on.
        return _gmqtt_threadsafe_publish_wrapper(
            self._client, self._loop, topic, payload, qos
        )

    def disconnect(self):
        self._run(self._client.disconnect())
        self._loop.call_soon_threadsafe(self._loop.stop)


def _gmqtt_sync_client_wrapper(cfg: BridgeConfig):
    """Build a gmqtt client and connect it, synchronously, like paho-mqtt did.

    paho-mqtt's `mqtt.Client(client_id=...)` followed by `client.connect(host,
    port)` is synchronous and returns a ready-to-use, connected client
    object. gmqtt has no synchronous constructor/connect path: `Client(...)`
    just builds the object and `connect()` is a coroutine. This wrapper
    constructs the gmqtt client and drives its `connect()` coroutine to
    completion on a persistent background event loop (see
    `_SyncMQTTClient`), then returns a synchronous-looking client whose
    `.publish()` the rest of this module can call exactly like the old
    paho-mqtt client's.
    """
    return _SyncMQTTClient(cfg.client_id, cfg.host, cfg.port)


def _default_client(cfg: BridgeConfig):
    return _gmqtt_sync_client_wrapper(cfg)


class TelemetryBridge:
    def __init__(self, cfg: BridgeConfig, client=None):
        self.cfg = cfg
        self.client = client if client is not None else _default_client(cfg)

    def publish_reading(self, tag: str, reading: dict) -> dict:
        topic = topic_for(self.cfg.base_topic, tag)
        payload = encode(reading)
        info = self.client.publish(topic, payload, qos=self.cfg.qos)
        return {
            "topic": topic,
            "payload": payload,
            "qos": self.cfg.qos,
            "rc": getattr(info, "rc", 0),
        }

    def publish_batch(self, readings: dict) -> list:
        """Publish {tag: reading} pairs, returning the per-message results."""
        return [self.publish_reading(tag, r) for tag, r in readings.items()]
