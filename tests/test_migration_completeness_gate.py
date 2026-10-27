"""The VCA's acceptance gate must not be satisfied by a migration that did not happen.

Measured before this gate existed: over 36 ``proteus_full`` runs the verifier
re-entered on 2 and reported ``converged=Y`` on all 36, including all 10
failures. It accepted a D3 clone whose ``sink.py`` still held eight references
to paho and a D2 clone whose ``client.py`` still referenced ``requests``.

The cause was ``patched_tests_ok``: the corpus suites inject fakes matching the
ORIGINAL library's signatures, so a clone that calls the fake correctly and the
real library incorrectly passes every test. These checks are the part of the
gate that does not route through those tests.

Each check must also stay SILENT where staying silent is correct -- a gate that
fires on a good migration costs a repair cycle and can only make the system
worse. The negative cases below are as load-bearing as the positive ones.
"""
import os
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart_loader.agents.migration_completeness import (  # noqa: E402
    completeness_errors, real_library_errors, source_import_errors,
    target_api_errors,
)


# ── Check 1: the source library is still imported ────────────────────────────

def test_residual_source_import_is_caught():
    """The D3 shape: migrated away from paho-mqtt, still imports paho."""
    code = textwrap.dedent("""
        import paho.mqtt.client as mqtt

        def _default_client():
            return mqtt.Client()
    """)
    errors = source_import_errors(code, "paho-mqtt")
    assert errors, "a live import of the abandoned library must fail the gate"
    assert "still imported" in errors[0]["message"]


def test_underscore_spelling_of_the_distribution_is_caught():
    """``simple-pid`` imports as ``simple_pid``; the hyphen form never matches."""
    assert source_import_errors("from simple_pid import PID\n", "simple-pid")


def test_clean_migration_is_silent():
    code = "import httpx\n\ndef _default_session():\n    return httpx.Client()\n"
    assert source_import_errors(code, "requests") == []


def test_source_named_only_in_a_docstring_is_silent():
    """A3's clone keeps the original's prose: 'datetime.fromisoformat -> ciso8601'.

    Matching the file as raw text scored a correct migration as unmigrated on
    all three seeds.
    """
    code = textwrap.dedent('''
        """Parse timestamps.

        Migrated from datetime.fromisoformat (stdlib) to ciso8601.
        """
        import ciso8601

        def parse(s):
            return ciso8601.parse_datetime(s)
    ''')
    assert source_import_errors(code, "datetime") == []


def test_stdlib_import_may_remain_when_the_replaced_call_is_gone():
    """A3 still handles datetime objects after migrating the parse call."""
    code = textwrap.dedent("""
        import datetime
        import ciso8601

        def parse(s) -> datetime.datetime:
            return ciso8601.parse_datetime(s)
    """)
    assert source_import_errors(code, "datetime") == []


def test_stdlib_replaced_call_still_present_is_caught():
    code = textwrap.dedent("""
        import datetime

        def parse(s):
            return datetime.datetime.fromisoformat(s)
    """)
    errors = source_import_errors(code, "datetime")
    assert errors and "still present" in errors[0]["message"]


# ── Check 2: the target is imported but never called ─────────────────────────

def test_imported_but_never_called_target_is_caught():
    """The exact connector output this check exists for, from D2."""
    code = textwrap.dedent("""
        def _default_session():
            import httpx
            _spt_r1 = requests.Session()
            return _spt_r1
    """)
    errors = target_api_errors(code, "httpx")
    assert errors, "importing the target without calling it is not a migration"
    assert "never referenced outside the import" in errors[0]["message"]


def test_target_actually_called_is_silent():
    code = "import httpx\n\ndef _default_session():\n    return httpx.Client()\n"
    assert target_api_errors(code, "httpx") == []


def test_from_import_of_the_target_counts_as_used():
    code = "from httpx import Client\n\ndef f():\n    return Client()\n"
    assert target_api_errors(code, "httpx") == []


def test_aliased_target_counts_as_used():
    code = "import httpx as hx\n\ndef f():\n    return hx.Client()\n"
    assert target_api_errors(code, "httpx") == []


def test_file_that_does_not_import_the_target_is_silent():
    """Not every file in a project needs the new library."""
    assert target_api_errors("def add(a, b):\n    return a + b\n", "httpx") == []


def test_syntax_error_is_left_to_the_syntax_check():
    assert target_api_errors("def f(\n", "httpx") == []
    assert completeness_errors("def f(\n", "requests", "httpx") == []


# ── Check 3: attribution, so a half-migrated project blames the right file ───

def test_runtime_check_is_silent_for_an_unregistered_program(tmp_path):
    assert real_library_errors(str(tmp_path), "NOT_A_PROGRAM", "x.py") == []


def test_runtime_check_is_silent_when_the_project_root_is_missing():
    assert real_library_errors("/no/such/dir", "D2_pump_station_scheduler",
                               "client.py") == []


def test_runtime_defect_is_not_charged_to_an_unrelated_file():
    """The verifier runs per file. A defect in bridge.py must not fail sink.py.

    Without this, a project that is half migrated spends one file's repair
    budget on a sibling nobody has touched yet.
    """
    clone = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "clones_final_v2", "proteus_full",
                         "B3_mqtt_telemetry_bridge_run1")
    if not os.path.isdir(clone):
        import pytest
        pytest.skip("clone archive not present")
    blamed = real_library_errors(clone, "B3_mqtt_telemetry_bridge", "bridge.py",
                                 "paho-mqtt", "aiomqtt")
    spared = real_library_errors(clone, "B3_mqtt_telemetry_bridge", "estimator.py",
                                 "paho-mqtt", "aiomqtt")
    assert blamed, "the file that made the bad call must be told about it"
    assert spared == [], "a file that did not make the call must not be blamed"


# ── The clones that actually slipped through ─────────────────────────────────

def test_the_clones_the_old_gate_accepted_are_now_rejected():
    """Real output the verifier signed off on with converged=Y.

    D3 run2: ``sink.py`` still imports paho after migrating to aiomqtt.
    D2 run2: ``client.py`` still references requests after migrating to httpx.
    """
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "clones_final_v2", "proteus_full")
    cases = [("D3_telemetry_gateway_run2", "sink.py", "paho-mqtt", "aiomqtt"),
             ("D2_pump_station_scheduler_run2", "client.py", "requests", "httpx")]
    for clone, filename, source, target in cases:
        path = os.path.join(root, clone, filename)
        if not os.path.isfile(path):
            import pytest
            pytest.skip("clone archive not present")
        code = open(path, encoding="utf-8").read()
        errors = completeness_errors(code, source, target, include_runtime=False)
        assert errors, f"{clone}/{filename} must not pass the acceptance gate"


# ── Check 4: the error contract the patch changed ────────────────────────────

def test_added_swallowing_handler_fails_the_gate():
    """The D3 shape. The original propagated; the patch returns a code instead."""
    from smart_loader.agents.migration_completeness import error_contract_errors
    before = textwrap.dedent("""
        def publish(client, topic, payload):
            return client.publish(topic, payload)
    """)
    after = textwrap.dedent("""
        def publish(client, topic, payload):
            try:
                client.publish(topic, payload)
                return Result(0)
            except Exception:
                return Result(1)
    """)
    errors = error_contract_errors(before, after)
    assert errors, "a swallowed error must not pass the acceptance gate"
    assert "re-raising" in errors[0]["message"]


def test_handler_that_reraises_passes_the_gate():
    from smart_loader.agents.migration_completeness import error_contract_errors
    before = "def send(c):\n    return c.send()\n"
    after = textwrap.dedent("""
        def send(c):
            try:
                return c.send()
            except ValueError as exc:
                raise RuntimeError("send failed") from exc
    """)
    assert error_contract_errors(before, after) == []


def test_handler_the_original_already_had_passes_the_gate():
    from smart_loader.agents.migration_completeness import error_contract_errors
    body = textwrap.dedent("""
        def send(c):
            try:
                return c.send()
            except Exception:
                return None
    """)
    assert error_contract_errors(body, body) == []


def test_completeness_errors_only_runs_check4_when_given_the_original():
    """Callers that cannot supply the original must not get a spurious error."""
    after = textwrap.dedent("""
        def send(c):
            try:
                return c.send()
            except Exception:
                return None
    """)
    assert completeness_errors(after, "requests", "httpx",
                               include_runtime=False) == []
    assert completeness_errors(after, "requests", "httpx", include_runtime=False,
                               source_code="def send(c):\n    return c.send()\n")


def test_verifier_passes_the_original_into_the_gate():
    """The wiring, not just the helper."""
    source = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "smart_loader", "agents", "verifier_agent.py"),
                  encoding="utf-8").read()
    call = source[source.index("_completeness.completeness_errors("):]
    call = call[:call.index(") + completeness_runtime_errors")]
    assert "source_code=source_code" in call, \
        "check 4 is inert unless the verifier hands it the pre-migration source"
