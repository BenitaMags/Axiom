"""Tests for anomaly_threshold_engine (dual-mode). Uses injected fake notifier/SMTP."""
from engine import AnomalyEngine
from engine_config import load
from hysteresis import Hysteresis
from notifier import EmailNotifier


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def notify(self, subject, body):
        self.sent.append((subject, body))


class FakeSMTP:
    def __init__(self):
        self.messages = []

    def sendmail(self, sender, recipients, message):
        self.messages.append((sender, tuple(recipients), message))


def test_hysteresis_deadband_prevents_chatter():
    h = Hysteresis(high=8.0, low=2.0)
    assert h.update(5.0) == "NORMAL"
    assert h.update(8.0) == "ALARM"
    assert h.update(5.0) == "ALARM"   # still latched inside deadband
    assert h.update(2.0) == "NORMAL"  # clears only at/below low


def test_config_rejects_inverted_band():
    try:
        load({"tags": [{"tag": "X", "high": 1.0, "low": 2.0}]})
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_engine_emits_events_on_transitions_only():
    cfg = load({"tags": [{"tag": "PT101", "high": 8.0, "low": 2.0}]})
    notifier = FakeNotifier()
    engine = AnomalyEngine(cfg, notifier=notifier)
    stream = [
        {"tag": "PT101", "value": 5.0},   # NORMAL, no event
        {"tag": "PT101", "value": 9.0},   # -> ALARM
        {"tag": "PT101", "value": 6.0},   # still ALARM, no event
        {"tag": "PT101", "value": 1.0},   # -> NORMAL
        {"tag": "UNKNOWN", "value": 99.0}  # untracked tag, ignored
    ]
    events = engine.process_stream(stream)
    assert [(e["state"]) for e in events] == ["ALARM", "NORMAL"]
    assert len(notifier.sent) == 2


def test_email_notifier_formats_message():
    smtp = FakeSMTP()
    n = EmailNotifier("scada@plant", ["ops@plant"], smtp=smtp)
    n.notify("PT101 ALARM", "high level")
    sender, recipients, message = smtp.messages[0]
    assert sender == "scada@plant" and recipients == ("ops@plant",)
    assert "Subject: PT101 ALARM" in message and message.endswith("high level")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
