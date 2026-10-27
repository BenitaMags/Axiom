"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for

import asyncio


def _axiom_async_client_lifecycle_wrapper(mqtt_module, cfg: BridgeConfig):
    """Bridge paho-mqtt's sync client lifecycle onto aiomqtt's async one.

    paho-mqtt's ``mqtt.Client(client_id=...)`` returns a plain object that is
    connected via a blocking ``client.connect(host, port)`` call and then used
    for the lifetime of the process; its ``publish()`` method is synchronous
    and returns an ``MQTTMessageInfo`` whose ``.rc`` attribute is the paho
    return code (0 == success).

    aiomqtt has no equivalent standalone "connect now, use later" client:
    ``aiomqtt.Client(...)`` must be entered via ``async with`` (which drives
    ``__aenter__``/``__aexit__`` as coroutines), and its ``publish()`` is
    itself a coroutine that returns ``None`` on success and raises
    ``aiomqtt.MqttError`` on failure -- there is no ``.rc`` return value.

    This wrapper reconciles the two models for the synchronous call sites in
    this module: it creates a private asyncio event loop, drives the
    aiomqtt client's ``__aenter__`` on it once (keeping the connection open
    for the wrapper's lifetime, mirroring paho's persistent-connection
    style), and returns a small facade object exposing a *synchronous*
    ``publish(topic, payload, qos)`` method. That method runs the aiomqtt
    publish coroutine to completion on the private loop and translates the
    outcome back into an object with an ``.rc`` attribute (0 for success, 1
    if an ``MqttError`` was raised), so existing rc-based callers keep
    working unchanged without needing to become async themselves.
    """

    class _SyncFacade:
        def __init__(self):
            self._loop = asyncio.new_event_loop()
            self._client = mqtt_module.Client(
                hostname=cfg.host, port=cfg.port, identifier=cfg.client_id
            )
            self._loop.run_until_complete(self._client.__aenter__())

        def publish(self, topic, payload, qos=0):
            class _Result:
                pass

            result = _Result()
            try:
                self._loop.run_until_complete(
                    self._client.publish(topic, payload, qos=qos)
                )
                result.rc = 0
            except Exception:
                result.rc = 1
            return result

        def close(self):
            self._loop.run_until_complete(self._client.__aexit__(None, None, None))
            self._loop.close()

    return _SyncFacade()


def _default_client(cfg: BridgeConfig):
    import paho.mqtt.client as mqtt  # lazy import for testability

    return _axiom_async_client_lifecycle_wrapper(mqtt, cfg)


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
