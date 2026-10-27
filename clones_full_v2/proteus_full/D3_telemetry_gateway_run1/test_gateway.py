"""Tests for telemetry_gateway (dual-mode). Fake reader + fake MQTT client."""
from estimator import Estimator
from gateway import TelemetryGateway
from sink import MqttSink


class FakeInfo:
    rc = 0


class FakeMqtt:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload):
        self.published.append((topic, payload))
        return FakeInfo()


def test_estimator_scales_and_smooths():
    est = Estimator(scale=0.1, window=3, roc_limit=100.0)
    assert est.update(100)["value"] == 10.0
    r = est.update(200)
    assert r["value"] == 20.0 and r["smoothed"] == 15.0  # mean(10,20)


def test_estimator_flags_rate_of_change_anomaly():
    est = Estimator(scale=1.0, window=5, roc_limit=5.0)
    assert est.update(10)["anomaly"] is False   # first sample, no roc
    assert est.update(12)["anomaly"] is False   # small step
    assert est.update(30)["anomaly"] is True    # jump of 18 > 5


def test_gateway_reads_estimates_publishes():
    readings = {"PT101": [100, 105, 400]}  # last is an anomalous jump
    seq = {k: iter(v) for k, v in readings.items()}
    mqtt = FakeMqtt()
    gw = TelemetryGateway(reader=lambda tag: next(seq[tag]),
                          sink=MqttSink(topic_base="plant/est", client=mqtt),
                          scale=0.1, window=5, roc_limit=5.0)
    r1 = gw.process("PT101")
    r2 = gw.process("PT101")
    r3 = gw.process("PT101")
    assert r1["value"] == 10.0 and r1["anomaly"] is False
    assert r3["anomaly"] is True                       # 40.0 vs 10.5 -> big roc
    assert mqtt.published[0][0] == "plant/est/PT101"   # topic includes tag
    assert len(mqtt.published) == 3


def test_gateway_keeps_per_tag_state():
    mqtt = FakeMqtt()
    values = {"A": iter([100, 110]), "B": iter([900])}
    gw = TelemetryGateway(reader=lambda t: next(values[t]),
                          sink=MqttSink(client=mqtt), scale=0.1, roc_limit=100.0)
    gw.process("A"); gw.process("B"); gw.process("A")
    assert set(gw.estimators.keys()) == {"A", "B"}


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
