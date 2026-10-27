"""engine -- streaming out-of-band anomaly detector with hysteresis.

Ties together per-tag hysteresis detection (first-party), configuration, and operator
notification. Emits one event per alarm state change and notifies on each transition.
"""
from hysteresis import Hysteresis


class AnomalyEngine:
    def __init__(self, config: dict, notifier=None):
        built = {}
        for tag_name, tag_cfg in config.items():
            built[tag_name] = Hysteresis(tag_cfg.high, tag_cfg.low)
        self.detectors = built
        self.notifier = notifier
        self.events = []

    def _lookup(self, tag):
        return self.detectors.get(tag)

    def process(self, reading: dict):
        """Process one {tag, value} reading; return an event on state change else None."""
        tag_name = reading["tag"]
        detector = self._lookup(tag_name)
        if detector is None:
            return None

        state_before = detector.state
        state_after = detector.update(reading["value"])

        if state_after == state_before:
            return None

        new_event = {"tag": tag_name, "state": state_after, "value": reading["value"]}
        self.events.append(new_event)

        if self.notifier is not None:
            subject = f"{tag_name} {state_after}"
            self.notifier.notify(subject, str(new_event))

        return new_event

    def process_stream(self, readings) -> list:
        """Process an iterable of readings, returning only the emitted events."""
        results = []
        for item in readings:
            outcome = self.process(item)
            if outcome is not None:
                results.append(outcome)
        return results
