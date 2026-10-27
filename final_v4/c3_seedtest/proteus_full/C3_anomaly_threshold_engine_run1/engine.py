"""engine -- streaming out-of-band anomaly detector with hysteresis.

Ties together per-tag hysteresis detection (first-party), configuration, and operator
notification. Emits one event per alarm state change and notifies on each transition.
"""
from event_utils import build_event, StateTransition


class AnomalyEngine:
    def __init__(self, config: dict, notifier=None):
        self.detectors = self._build_detectors(config)
        self.notifier = notifier
        self.events = []

    @staticmethod
    def _build_detectors(config: dict) -> dict:
        from hysteresis import Hysteresis
        return {tag: Hysteresis(c.high, c.low) for tag, c in config.items()}

    def _lookup_detector(self, tag):
        return self.detectors.get(tag)

    def _emit(self, transition: StateTransition):
        event = build_event(transition)
        self.events.append(event)
        if self.notifier is not None:
            self.notifier.notify(
                f"{transition.tag} {transition.current}", str(event)
            )
        return event

    def process(self, reading: dict):
        """Process one {tag, value} reading; return an event on state change else None."""
        tag = reading["tag"]
        detector = self._lookup_detector(tag)
        if detector is None:
            return None

        previous = detector.state
        current = detector.update(reading["value"])

        if current == previous:
            return None

        transition = StateTransition(
            tag=tag, previous=previous, current=current, value=reading["value"]
        )
        return self._emit(transition)

    def process_stream(self, readings) -> list:
        """Process an iterable of readings, returning only the emitted events."""
        emitted = []
        for reading in readings:
            event = self.process(reading)
            if event is not None:
                emitted.append(event)
        return emitted
