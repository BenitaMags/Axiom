"""engine -- streaming out-of-band anomaly detector with hysteresis.

Ties together per-tag hysteresis detection (first-party), configuration, and operator
notification. Emits one event per alarm state change and notifies on each transition.
"""
from hysteresis import Hysteresis


class AnomalyEngine:
    def __init__(self, config: dict, notifier=None):
        self.detectors = {tag: Hysteresis(c.high, c.low) for tag, c in config.items()}
        self.notifier = notifier
        self.events = []

    def process(self, reading: dict):
        """Process one {tag, value} reading; return an event on state change else None."""
        tag = reading["tag"]
        detector = self.detectors.get(tag)
        if detector is None:
            return None
        previous = detector.state
        current = detector.update(reading["value"])
        if current == previous:
            return None
        event = {"tag": tag, "state": current, "value": reading["value"]}
        self.events.append(event)
        if self.notifier is not None:
            self.notifier.notify(f"{tag} {current}", str(event))
        return event

    def process_stream(self, readings) -> list:
        """Process an iterable of readings, returning only the emitted events."""
        emitted = []
        for reading in readings:
            event = self.process(reading)
            if event is not None:
                emitted.append(event)
        return emitted
