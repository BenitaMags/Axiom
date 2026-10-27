"""engine -- streaming out-of-band anomaly detector with hysteresis.

Ties together per-tag hysteresis detection (first-party), configuration, and operator
notification. Emits one event per alarm state change and notifies on each transition.
"""
from hysteresis import Hysteresis


class AnomalyEngine:
    def __init__(self, config: dict, notifier=None):
        built = {}
        for tag_name, cfg_entry in config.items():
            built[tag_name] = Hysteresis(cfg_entry.high, cfg_entry.low)
        self.detectors = built
        self.notifier = notifier
        self.events = []

    def process(self, reading: dict):
        """Process one {tag, value} reading; return an event on state change else None."""
        tag_name = reading["tag"]
        if tag_name not in self.detectors:
            return None
        detector = self.detectors[tag_name]
        state_before = detector.state
        state_after = detector.update(reading["value"])
        if state_after == state_before:
            return None
        new_event = {"tag": tag_name, "state": state_after, "value": reading["value"]}
        self.events.append(new_event)
        if self.notifier is not None:
            self.notifier.notify(f"{tag_name} {state_after}", str(new_event))
        return new_event

    def process_stream(self, readings) -> list:
        """Process an iterable of readings, returning only the emitted events."""
        results = []
        for item in readings:
            outcome = self.process(item)
            if outcome is not None:
                results.append(outcome)
        return results
