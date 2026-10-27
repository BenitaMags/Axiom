"""Tests for mqtt_telemetry_bridge (dual-mode). Uses an injected fake MQTT client."""
from bridge import TelemetryBridge
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class FakeInfo:
    rc = 0


class FakeClient:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, payload, qos))
        return FakeInfo()


def test_config_from_dict_ignores_unknown_keys():
    cfg = BridgeConfig.from_dict({"host": "broker", "qos": 2, "extra": "ignored"})
    assert cfg.host == "broker" and cfg.qos == 2
    assert cfg.base_topic == "plant/telemetry"  # default preserved


def test_topic_and_payload():
    assert topic_for("plant/telemetry", "PT101") == "plant/telemetry/PT101"
    assert encode({"value": 1.0, "ts": "t"}) == b'{"value":1.0,"ts":"t","q":"GOOD"}'


def test_publish_batch_uses_config_qos():
    cfg = BridgeConfig(base_topic="plant/x", qos=2)
    client = FakeClient()
    bridge = TelemetryBridge(cfg, client=client)
    results = bridge.publish_batch({
        "PT101": {"value": 12.0, "ts": "t1"},
        "TT200": {"value": 30.0, "ts": "t2", "quality": "UNCERTAIN"},
    })
    assert [r["topic"] for r in results] == ["plant/x/PT101", "plant/x/TT200"]
    assert all(r["qos"] == 2 for r in results)
    assert client.published[0][0] == "plant/x/PT101"
    assert b'"q":"UNCERTAIN"' in client.published[1][1]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
