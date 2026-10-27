"""Dispatch table mapping detector states to their transition rules."""


def _from_normal(detector, value: float):
    if value >= detector.high:
        return "ALARM"
    return None


def _from_alarm(detector, value: float):
    if value <= detector.low:
        return "NORMAL"
    return None


STATE_TRANSITIONS = {
    "NORMAL": _from_normal,
    "ALARM": _from_alarm,
}
