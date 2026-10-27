"""Tests for pump_station_scheduler (dual-mode). Fake HTTP session; DI scheduler."""
from client import ScadaClient
from scheduler import PumpScheduler
from station import PumpStation


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, demand, runtimes):
        self.demand = demand
        self.runtimes = runtimes
        self.posts = []

    def get(self, url, timeout=None):
        if url.endswith("/demand"):
            return FakeResponse({"demand": self.demand})
        if url.endswith("/runtimes"):
            return FakeResponse({"runtimes": self.runtimes})
        raise AssertionError(url)

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json["on"]))
        return FakeResponse({}, status=200)


def test_required_count_stages_by_capacity():
    s = PumpScheduler(n_pumps=3, pump_capacity=50.0)
    assert s.required_count(0) == 0
    assert s.required_count(1) == 1
    assert s.required_count(50) == 1
    assert s.required_count(51) == 2
    assert s.required_count(1000) == 3  # capped at n_pumps


def test_select_prefers_least_run_pumps():
    s = PumpScheduler(n_pumps=3, pump_capacity=50.0)
    # demand needs 2 pumps; pumps 2 and 0 have the least runtime.
    plan = s.select(80.0, runtimes=[10.0, 99.0, 5.0])
    assert plan == {0: True, 1: False, 2: True}


def test_station_cycle_issues_commands():
    sess = FakeSession(demand=80.0, runtimes=[10.0, 99.0, 5.0])
    station = PumpStation(ScadaClient("http://scada", session=sess))
    plan = station.cycle()
    assert plan == {0: True, 1: False, 2: True}
    # one POST per pump, carrying the on/off decision
    assert ("http://scada/pump/2", True) in sess.posts
    assert ("http://scada/pump/1", False) in sess.posts
    assert len(sess.posts) == 3


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
