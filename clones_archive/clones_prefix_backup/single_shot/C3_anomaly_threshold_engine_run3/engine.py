"""engine -- streaming out-of-band anomaly detector with hysteresis.

Ties together per-tag hysteresis detection (first-party), configuration, and operator
notification. Emits one event per alarm state change and notifies on each transition.
"""
from hysteresis import Hysteresis


class AnomalyEngine:
    def __init__(self, config: dict, notifier=None):
        built = {}
        for tag, cfg in config.items():
            built[tag] = Hysteresis(cfg.high, cfg.low)
        self.detectors = built
        self.notifier = notifier
        self.events = []

    def process(self, reading: dict):
        """Process one {tag, value} reading; return an event on state change else None."""
        tag_name = reading["tag"]
        matched_detector = self.detectors.get(tag_name)
        if matched_detector is None:
            return None

        prior_state = matched_detector.state
        new_state = matched_detector.update(reading["value"])

        if new_state == prior_state:
            return None

        emitted_event = {"tag": tag_name, "state": new_state, "value": reading["value"]}
        self.events.append(emitted_event)

        if self.notifier is not None:
            subject_line = f"{tag_name} {new_state}"
            self.notifier.notify(subject_line, str(emitted_event))

        return emitted_event

    def process_stream(self, readings) -> list:
        """Process an iterable of readings, returning only the emitted events."""
        results = []
        for single_reading in readings:
            outcome = self.process(single_reading)
            if outcome is not None:
                results.append(outcome)
        return results
