"""bridge -- publish sensor telemetry to an MQTT broker.

The MQTT client is the substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async context-managed). The connection/publish models differ enough to
require glue -- hence Category B. The client is dependency-injected for testing.
"""
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


def _axiom_mqtt_client_ctor_wrapper(mqtt_module, cfg: BridgeConfig):
    """Construct an MQTT client object across the paho-mqtt / aiomqtt API gap.

    paho-mqtt's ``mqtt.Client`` accepts a ``client_id`` keyword argument to
    name the connection. aiomqtt's ``Client`` constructor exposes the same
    concept but under the parameter name ``identifier`` instead of
    ``client_id``. This wrapper takes the already-imported client module
    (whichever one is bound as ``mqtt`` at the call site) plus the bridge
    config, and forwards ``cfg.client_id`` under the correct keyword for
    that module so callers don't need to know which constructor signature
    is in effect.

    Note: this only fixes the keyword-argument mismatch. It does not (and
    cannot) restore the synchronous "construct, then connect(), then reuse
    the same object across publish() calls" pattern the rest of this module
    relies on, since aiomqtt's Client is meant to be used as an async
    context manager (``async with Client(...) as client``) rather than via
    a bare constructor + later ``.connect()``. Bridging that lifecycle
    difference is outside the scope of this constructor-only wrapper.
    """
    return mqtt_module.Client(identifier=cfg.client_id)


def _default_client(cfg: BridgeConfig):
    import paho.mqtt.client as mqtt  # lazy import for testability

    client = _axiom_mqtt_client_ctor_wrapper(mqtt, cfg)
    client.connect(cfg.host, cfg.port)
    return client


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
