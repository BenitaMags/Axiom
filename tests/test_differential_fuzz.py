"""The harness must refuse to score what it did not execute.

A differential that injects a double reproduces the exact blind spot this
project exists to close: the double bypasses whatever the Connector wrote into
`_default_client`, the migrated code never runs, both sides trivially agree,
and the harness reports a confident MATCHES about code it never touched. That
is how the corpus's own tests pass while shipping a broken adapter.

Two guards, both learned the hard way while building this:

NOT-REACHED. "Migrated" is defined structurally -- a function the clone defines
and the original does not. Matching an `_axiom`/`_spt` prefix was too loose:
B3's fabricating wrapper is a method plainly called `publish`, so a stray
`_spt_` helper elsewhere made an untested scenario look reached.

The tracer is qualified BY FILE. This harness's own doubles define `publish`
and `connect` too, and a bare-name tracer counted an injected double's method
as the clone's wrapper running -- scoring MATCHES on three findings it had not
tested.

INVALID. A scenario that misuses the API raises the same TypeError on both
sides and would otherwise score a meaningless MATCHES.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import differential_fuzz as D  # noqa: E402


def test_observe_records_a_returned_value():
    kind, typename, _ = D.observe(lambda: 7)
    assert (kind, typename) == ("returned", "int")


def test_observe_records_the_exception_class_a_caller_would_catch():
    def boom():
        raise ValueError("nope")
    kind, typename, message = D.observe(boom)
    assert (kind, typename) == ("raised", "ValueError")
    assert "nope" in message


def test_an_exception_class_change_is_a_divergence():
    """The whole point: comparing values alone would miss it."""
    def a():
        raise KeyError("x")

    def b():
        raise LookupError("x")
    assert D.observe(a) != D.observe(b)


def test_the_tracer_is_qualified_by_file():
    """A bare name let this harness's own double stand in for the clone's."""
    _, executed = D.observe_traced(lambda: D.RcClient(rc=3).publish("t", b""))
    assert all(isinstance(entry, tuple) and len(entry) == 2 for entry in executed)
    files = {path for path, _ in executed}
    assert any(path.endswith("differential_fuzz.py") for path in files)


def test_harness_misuse_exceptions_are_named():
    """These mean the scenario is wrong, not the clone."""
    for name in ("TypeError", "KeyError", "AttributeError"):
        assert name in D._HARNESS_MISUSE


def test_clone_defined_names_reads_functions_and_methods(tmp_path):
    (tmp_path / "m.py").write_text(
        "def top():\n    pass\n\nclass C:\n    def method(self):\n        pass\n")
    (tmp_path / "test_m.py").write_text("def test_ignored():\n    pass\n")
    names = D._clone_defined_names(str(tmp_path))
    assert {"top", "method"} <= names
    assert "test_ignored" not in names, "test files are not program code"


def test_every_scenario_names_the_finding_it_tests():
    for program, scenarios in D.SCENARIOS.items():
        for entry in scenarios:
            finding, modules, fn = entry[0], entry[1], entry[2]
            assert finding.strip(), program
            assert modules, program
            assert callable(fn) or fn == "IMPORT_TIME", program


def test_a_scenario_only_scores_the_runs_it_was_written_for():
    """C3 r1 and r3 were rated MAJOR for a hoisted lazy import; r2 for
    something else entirely. Scoring r2 with r1's scenario would report a
    false positive on a claim that was never tested."""
    assert D.applies_to("C3 r1/r3: can the module still be imported?", "1")
    assert D.applies_to("C3 r1/r3: can the module still be imported?", "3")
    assert not D.applies_to("C3 r1/r3: can the module still be imported?", "2")
    # A label with no run numbers applies everywhere.
    assert D.applies_to("something general", "2")


def test_the_import_blocker_actually_blocks():
    hook = D._Blocked(["apprise"])
    sys.meta_path.insert(0, hook)
    try:
        import importlib
        try:
            importlib.import_module("apprise")
            raised = False
        except ImportError:
            raised = True
    finally:
        sys.meta_path.remove(hook)
    assert raised, "the blocker must make the package unavailable"


def test_the_rc_doubles_differ_in_the_way_the_findings_describe():
    """One reports a real non-zero rc; the other has no .rc at all."""
    info = D.RcClient(rc=7).publish("t", b"")
    assert getattr(info, "rc", 0) == 7
    plain = D.NoRcClient().publish("t", b"")
    assert getattr(plain, "rc", 0) == 0


def test_a_scenario_compares_behaviour_not_migrated_types():
    """After a library swap the returned object is a different class by
    construction, so comparing its type reports a divergence on every correct
    migration -- the harness's own version of the mistake it exists to catch.

    B2 r2 hit this: the finding claims the rewrite RAISES where the original
    returned a client, and the first version of the scenario compared
    `ModbusTcpClient` with `ModbusClient` and called it a divergence. Rewritten
    to compare raised-vs-returned, the row is a false positive.
    """
    import inspect
    body = inspect.getsource(D._b2_failed_connect)
    assert "returned a client" in body
    assert "hasattr" in body, "compare capability, not class identity"


def test_the_stubs_model_the_difference_the_findings_describe():
    """The outgoing library reports a status; the incoming one does not."""
    paho = D._paho_stub(rc=7)["paho.mqtt.client"]
    info = paho.Client().publish("t", b"x")
    assert getattr(info, "rc", None) == 7

    amqtt = D._async_mqtt_stub("amqtt")["amqtt"]
    assert hasattr(amqtt, "MQTTClient") and hasattr(amqtt, "MqttError")

    pym = D._pymodbus_stub(connect_ok=False)["pymodbus.client"]
    assert pym.ModbusTcpClient("h").connect() is False, "returns False, never raises"

    smtp = D._smtplib_stub(fail=True)["smtplib"]
    try:
        smtp.SMTP("localhost", 25)
        eager_failure = False
    except ConnectionRefusedError:
        eager_failure = True
    assert eager_failure, "the original connected eagerly at construction"
