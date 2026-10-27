"""engine -- streaming out-of-band anomaly detector with hysteresis.

Ties together per-tag hysteresis detection (first-party), configuration, and operator
notification. Emits one event per alarm state change and notifies on each transition.
"""
from hysteresis import Hysteresis


class AnomalyEngine:
    def __init__(self, config: dict, notifier=None):
        built = {}
        for tag, c in config.items():
            built[tag] = Hysteresis(c.high, c.low)
        self.detectors = built
        self.notifier = notifier
        self.events = []

    def process(self, reading: dict):
        """Process one {tag, value} reading; return an event on state change else None."""
        tag = reading["tag"]
        if tag not in self.detectors:
            return None
        detector = self.detectors[tag]
        prior_state = detector.state
        new_state = detector.update(reading["value"])
        if new_state == prior_state:
            return None
        event = {"tag": tag, "state": new_state, "value": reading["value"]}
        self.events.append(event)
        if self.notifier is not None:
            self.notifier.notify(f"{tag} {new_state}", str(event))
        return event

    def process_stream(self, readings) -> list:
        """Process an iterable of readings, returning only the emitted events."""
        results = []
        for reading in readings:
            outcome = self.process(reading)
            if outcome is not None:
                results.append(outcome)
        return results
