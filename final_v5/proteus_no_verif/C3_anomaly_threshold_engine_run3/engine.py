"""engine -- streaming out-of-band anomaly detector with hysteresis.

Ties together per-tag hysteresis detection (first-party), configuration, and operator
notification. Emits one event per alarm state change and notifies on each transition.
"""


def _build_detectors(config: dict) -> dict:
    from hysteresis import Hysteresis
    return {tag: Hysteresis(c.high, c.low) for tag, c in config.items()}


def _make_event(tag, state, value) -> dict:
    return {"tag": tag, "state": state, "value": value}


class AnomalyEngine:
    def __init__(self, config: dict, notifier=None):
        self.detectors = _build_detectors(config)
        self.notifier = notifier
        self.events = []

    def _notify(self, tag, current, event):
        if self.notifier is not None:
            self.notifier.notify(f"{tag} {current}", str(event))

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

        event = _make_event(tag, current, reading["value"])
        self.events.append(event)
        self._notify(tag, current, event)
        return event

    def process_stream(self, readings) -> list:
        """Process an iterable of readings, returning only the emitted events."""
        return [
            event
            for event in (self.process(reading) for reading in readings)
            if event is not None
        ]
