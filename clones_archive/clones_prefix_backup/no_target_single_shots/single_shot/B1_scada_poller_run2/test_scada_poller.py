"""Tests for scada_poller (dual-mode). Uses an injected fake HTTP session."""
from scada_poller import ScadaPoller


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, table):
        self.table = table
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append((url, timeout))
        tag = url.rsplit("/", 1)[-1]
        return self.table[tag]


def test_reads_and_normalizes():
    sess = FakeSession({
        "PT101": FakeResponse({"value": 12.0, "quality": "GOOD"}),
        "TT200": FakeResponse({"value": 300.5}),  # quality defaults
    })
    poller = ScadaPoller("http://scada/", session=sess, timeout=1.5)
    out = poller.poll(["PT101", "TT200"])
    assert out == [
        {"tag": "PT101", "value": 12.0, "quality": "GOOD"},
        {"tag": "TT200", "value": 300.5, "quality": "GOOD"},
    ]
    assert sess.calls[0] == ("http://scada/tags/PT101", 1.5)


def test_http_error_propagates():
    sess = FakeSession({"BAD": FakeResponse({}, status=500)})
    poller = ScadaPoller("http://scada", session=sess)
    try:
        poller.read_tag("BAD")
        assert False, "expected error"
    except RuntimeError:
        pass


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
