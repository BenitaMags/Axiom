"""An adapter that returns the target library's own object adapts nothing.

Measured on D2. The connector replaced ``requests.Session()`` with
``httpx.Client(follow_redirects=True, timeout=...)`` and stopped there, so
``response.raise_for_status()`` in the program's own methods began raising
``httpx.HTTPStatusError`` where it raised ``requests.exceptions.HTTPError``.
A caller written ``except requests.exceptions.HTTPError`` stops catching the
failure; connection errors and timeouts change class the same way. A blind
judge rated it MAJOR, and every arm's D2 clone has the same defect -- it is
systematic.

Nothing caught it, for one reason: every error-contract rule in the prompt is
written about HANDLERS, and this adapter writes no handler. Layer 7 compares
handlers too, and D2 has none on either side, so it correctly reports no
divergence. The exception classes change at a call site nobody wrapped.

The discriminator these tests pin is the contrast between the three real
adapters this corpus produced: D1 returned ``_ModbusTkClientShim(master)`` and
C3 returned ``_AppriseSMTPAdapter(...)`` -- first-party classes whose methods
can translate -- while D2 returned the library's own object.
"""
import os
import sys
import textwrap

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from smart_loader.agents.connector_agent import _raw_client_returns  # noqa: E402

CLONES = os.path.join(ROOT, "final_v4", "round3")


def _src(code):
    return textwrap.dedent(code).lstrip()


# ------------------------------------------------------------- the defect ---

def test_returning_the_library_object_is_flagged():
    found = _raw_client_returns(_src("""
        def build_default_session():
            import httpx
            return httpx.Client(follow_redirects=True)
    """), "httpx", ["build_default_session"])
    assert len(found) == 1
    assert "build_default_session()" in found[0]
    assert "httpx" in found[0]


def test_returning_it_through_a_variable_is_the_same_defect():
    found = _raw_client_returns(_src("""
        import httpx

        def make_client():
            client = httpx.Client()
            return client
    """), "httpx", ["make_client"])
    assert len(found) == 1


def test_a_from_import_binding_is_followed():
    found = _raw_client_returns(_src("""
        from httpx import Client

        def make_client():
            return Client()
    """), "httpx", ["make_client"])
    assert len(found) == 1


def test_a_submodule_alias_is_followed():
    """`import modbus_tk.modbus_tcp as modbus_tcp` binds a name to the target."""
    found = _raw_client_returns(_src("""
        import modbus_tk.modbus_tcp as modbus_tcp

        def make_master(host):
            return modbus_tcp.TcpMaster(host=host)
    """), "modbus-tk", ["make_master"])
    assert len(found) == 1


# -------------------------------------------------------- correct adapters ---

def test_returning_a_first_party_adapter_is_accepted():
    assert _raw_client_returns(_src("""
        import httpx

        class _Adapter:
            def __init__(self, client):
                self._client = client

            def get(self, url, **kw):
                try:
                    return self._client.get(url, **kw)
                except httpx.HTTPStatusError as exc:
                    raise OSError(str(exc)) from exc

        def build_default_session():
            return _Adapter(httpx.Client())
    """), "httpx", ["build_default_session"]) == []


def test_an_unrelated_library_is_not_flagged():
    assert _raw_client_returns(_src("""
        import json

        def build():
            return json.JSONDecoder()
    """), "httpx", ["build"]) == []


def test_only_the_named_wrappers_are_inspected():
    src = _src("""
        import httpx

        def not_a_wrapper():
            return httpx.Client()
    """)
    assert _raw_client_returns(src, "httpx", ["some_other_wrapper"]) == []
    assert len(_raw_client_returns(src, "httpx", [])) == 1   # no filter = all


def test_unparseable_source_is_not_guessed_at():
    assert _raw_client_returns("def broken(:", "httpx", []) == []


# ------------------------------------------- the three real D2/D1/C3 clones ---

@pytest.mark.skipif(not os.path.isdir(CLONES), reason="round3 clones absent")
@pytest.mark.parametrize("path,winner,flagged", [
    ("proteus_full/D2_pump_station_scheduler_run1/http_session_factory.py",
     "httpx", True),
    ("D1_boiler_control_loop_run1/modbus_shim.py", "modbus-tk", False),
    ("proteus_full/C3_anomaly_threshold_engine_run1/notifier.py",
     "apprise", False),
])
def test_against_the_adapters_this_corpus_actually_produced(path, winner, flagged):
    full = os.path.join(CLONES, path)
    if not os.path.isfile(full):
        pytest.skip(f"{path} not present")
    source = open(full, encoding="utf-8").read()
    assert bool(_raw_client_returns(source, winner, [])) is flagged
