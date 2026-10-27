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

    def process(self, reading: dict):
        """Process one {tag, value} reading; return an event on state change else None."""
        tag_name = reading["tag"]
        if tag_name not in self.detectors:
            return None
        detector = self.detectors[tag_name]
        prior_state = detector.state
        new_state = detector.update(reading["value"])
        if new_state == prior_state:
            return None
        outcome = {"tag": tag_name, "state": new_state, "value": reading["value"]}
        self.events.append(outcome)
        if self.notifier is not None:
            self.notifier.notify(f"{tag_name} {new_state}", str(outcome))
        return outcome

    def process_stream(self, readings) -> list:
        """Process an iterable of readings, returning only the emitted events."""
        results = []
        for item in readings:
            outcome = self.process(item)
            if outcome is not None:
                results.append(outcome)
        return results
