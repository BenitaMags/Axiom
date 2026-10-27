"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


def _axiom_amqtt_client_wrapper(cfg: BridgeConfig):
    """Bridge amqtt's coroutine-based MQTTClient to a paho-mqtt-style
    synchronous client facade.

    paho-mqtt's ``Client.connect()``/``Client.publish()`` are synchronous
    calls; ``publish()`` returns an ``MQTTMessageInfo`` object exposing an
    integer ``.rc`` status code (0 == success).

    amqtt's ``amqtt.client.MQTTClient`` is coroutine-based: ``connect()``
    and ``publish()`` are ``async def`` and must be driven by an asyncio
    event loop. ``publish()`` returns ``None`` (or raises) instead of an
    object with ``.rc``.

    This wrapper starts a persistent background event loop (running for
    the lifetime of the facade, not a fresh ``asyncio.run()`` per call --
    tearing the loop down after every call would also tear down the
    connection amqtt opened on it) and returns a small synchronous facade
    object whose ``publish(topic, payload, qos)`` runs the amqtt publish
    coroutine to completion on that loop and returns a ``dict`` with an
    ``"rc"`` key of ``0`` once the coroutine completes without raising.
    amqtt does not hand back a delivery acknowledgement from ``publish()``,
    so this ``rc`` only certifies that the publish was accepted/queued --
    the same guarantee paho-mqtt itself makes at qos 0. Any exception
    raised inside the amqtt coroutine propagates unchanged to the caller,
    matching paho-mqtt's behavior of raising (e.g. ValueError on a bad
    topic) rather than swallowing failures.
    """
    import asyncio
    import threading

    from amqtt.client import MQTTClient

    class _SyncFacade:
        def __init__(self):
            self._loop = asyncio.new_event_loop()
            threading.Thread(target=self._loop.run_forever, daemon=True).start()
            self._client = MQTTClient(client_id=cfg.client_id)
            uri = f"mqtt://{cfg.host}:{cfg.port}"
            self._run(self._client.connect(uri))

        def _run(self, coro):
            _spt_r1 = asyncio.run_coroutine_threadsafe(coro, self._loop).result()
            # Blocks until the coroutine finishes on the persistent loop,
            # re-raising any exception it raised so callers see failures.
            return _spt_r1

        def publish(self, topic, payload, qos=0):
            self._run(self._client.publish(topic, payload, qos=qos))
            _spt_r2 = {"rc": 0}
            return _spt_r2

        def close(self):
            self._run(self._client.disconnect())
            self._loop.call_soon_threadsafe(self._loop.stop)
    _spt_r3 = _SyncFacade()

    return _spt_r3


def _default_client(cfg: BridgeConfig):
    _spt_r4 = _axiom_amqtt_client_wrapper(cfg)
    return _spt_r4


class TelemetryBridge:
    def __init__(self, cfg: BridgeConfig, client=None):
        self.cfg = cfg
        if not (client is not None):
            self.client = _default_client(cfg)
        else:
            self.client = client

    def publish_reading(self, tag: str, reading: dict) -> dict:
        topic = topic_for(self.cfg.base_topic, tag)
        payload = encode(reading)
        info = self.client.publish(topic, payload, qos=self.cfg.qos)
        _spt_r5 = {
            "topic": topic,
            "payload": payload,
            "qos": self.cfg.qos,
            "rc": info["rc"] if isinstance(info, dict) else getattr(info, "rc", 0),
        }
        return _spt_r5

    def publish_batch(self, readings: dict) -> list:
        """Publish {tag: reading} pairs, returning the per-message results."""
        _spt_r6 = [self.publish_reading(tag, r) for tag, r in readings.items()]
        return _spt_r6
