"""A distribution name is not its import root, and guessing broke the migration.

``import_replacements`` was keyed on ``distribution.replace("-", "_")``. That
is correct for ``simple-pid`` -> ``simple_pid`` and wrong for ``paho-mqtt``,
whose module root is ``paho``. Keyed on a root that does not exist, the libCST
rewrite matched nothing and the dead-import sweep beneath it — keyed on the
same value — matched nothing either.

The visible result was not a partial migration but an inverted one: B3 and D3
kept ``import paho.mqtt.client as mqtt`` in all six Proteus runs, and the
model, forbidden by the IMPORT LOCK from touching imports, wrote an adapter
that accepts the old module as an argument.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart_loader.agents.connector_agent import _resolve_import_root
from smart_loader.core.ast_rewrite import apply_import_replacements

B3_SOURCE = '''\
from mqtt_config import BridgeConfig


def _default_client(cfg):
    import paho.mqtt.client as mqtt  # lazy import for testability

    return mqtt.Client(client_id=cfg.client_id)
'''

C1_SOURCE = '''\
def _default_pid(kp, ki, kd):
    import simple_pid

    return simple_pid.PID(kp, ki, kd)
'''


def test_hyphenated_distribution_whose_root_is_the_first_segment():
    """``paho-mqtt`` imports as ``paho`` — the case that was broken."""
    assert _resolve_import_root("paho-mqtt", B3_SOURCE) == "paho"


def test_hyphenated_distribution_whose_root_is_the_underscore_form():
    """``simple-pid`` imports as ``simple_pid`` — must not regress to ``simple``."""
    assert _resolve_import_root("simple-pid", C1_SOURCE) == "simple_pid"


def test_plain_distribution_is_unchanged():
    assert _resolve_import_root("pymodbus", "import pymodbus\n") == "pymodbus"


def test_unknown_root_falls_back_to_the_declared_value():
    """Never invent a root: with no evidence, the declared value stands."""
    assert _resolve_import_root("some-lib", "import os\n",
                                declared="some_lib") == "some_lib"


def test_the_rewrite_now_actually_fires_on_a_lazy_import():
    """End to end: the resolved key makes libCST reach a function-scope import.

    Nine of the twelve corpus programs import the old library inside a
    function ("lazy import for testability"), so this is the common case, not
    an edge case.
    """
    root = _resolve_import_root("paho-mqtt", B3_SOURCE)
    rewritten = apply_import_replacements(B3_SOURCE, {root: "aiomqtt"})
    assert "import paho.mqtt.client" not in rewritten, (
        "the lazy import survived the rewrite")
    assert "from mqtt_config import BridgeConfig" in rewritten, (
        "a first-party import was collateral damage")


def test_the_old_guess_would_have_missed_it():
    """Guard the regression itself: the previous key matched nothing."""
    old_key = "paho-mqtt".replace("-", "_")
    unchanged = apply_import_replacements(B3_SOURCE, {old_key: "aiomqtt"})
    assert "import paho.mqtt.client" in unchanged


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))


# ── the verifier's patch-integrity check ─────────────────────────────────────

def test_patch_integrity_accepts_a_correct_hyphenated_migration():
    """Regression for the false positive this check produced on B3.

    The live run migrated paho-mqtt -> amqtt correctly — the old import gone,
    the adapter importing ``amqtt.client`` — and patch integrity still reported
    "Selected source import 'paho_mqtt' is not present in the original file".
    Because that branch fired, the ``elif`` that verifies the source import
    actually CHANGED was never reached, so the real invariant was silently
    disabled for every hyphenated distribution.
    """
    from smart_loader.agents.verifier_agent import _patch_integrity_errors
    from smart_loader.core.state import LoadDecision

    patched = '''\
from mqtt_config import BridgeConfig


def _default_client(cfg):
    from amqtt.client import MQTTClient

    return MQTTClient(client_id=cfg.client_id)
'''
    decision = LoadDecision(
        role="MQTT client", original="paho-mqtt", winner="amqtt",
        rationale="test", confidence="LOW", score={},
    )
    errors = _patch_integrity_errors(B3_SOURCE, patched, [decision])
    messages = " ".join(e["message"] for e in errors)
    assert "is not present in the original file" not in messages, (
        f"correct migration rejected by patch integrity: {messages}")


def test_patch_integrity_still_rejects_an_untouched_source_import():
    """The real invariant must survive the fix: an unchanged import fails."""
    from smart_loader.agents.verifier_agent import _patch_integrity_errors
    from smart_loader.core.state import LoadDecision

    patched = B3_SOURCE.replace("return mqtt.Client(client_id=cfg.client_id)",
                                "return mqtt.Client(client_id=cfg.client_id)  # noqa")
    decision = LoadDecision(
        role="MQTT client", original="paho-mqtt", winner="amqtt",
        rationale="test", confidence="LOW", score={},
    )
    errors = _patch_integrity_errors(B3_SOURCE, patched, [decision])
    assert errors, "an unchanged source import must still be rejected"


# ── distributions whose import root shares no spelling with their name ──────
#
# `python-statemachine` imports as `statemachine`. Every string transformation
# of the name fails on it, and the fallback produced `python_statemachine` --
# which matches no import in any file, so `remaining_source_imports` looked for
# the wrong module, found nothing, and the post-condition meant to reject an
# unfinished migration would have passed an unmigrated C2 clone instead.

def test_a_prefixed_distribution_resolves_to_its_real_root():
    pytest = __import__("pytest")
    from smart_loader.core.ast_rewrite import resolve_import_root
    try:
        import statemachine                                   # noqa: F401
    except ImportError:
        pytest.skip("python-statemachine not installed")
    assert resolve_import_root(
        "python-statemachine",
        "from statemachine import StateMachine, State") == "statemachine"


def test_it_resolves_without_the_source_file_too():
    """Metadata beats a string guess even with nothing to match against."""
    pytest = __import__("pytest")
    from smart_loader.core.ast_rewrite import resolve_import_root
    try:
        import statemachine                                   # noqa: F401
    except ImportError:
        pytest.skip("python-statemachine not installed")
    assert resolve_import_root("python-statemachine", "") == "statemachine"


def test_the_earlier_cases_still_resolve():
    """Metadata must not regress the prefix- and underscore-form cases."""
    from smart_loader.core.ast_rewrite import resolve_import_root
    assert resolve_import_root(
        "paho-mqtt", "import paho.mqtt.client as mqtt") == "paho"
    assert resolve_import_root(
        "simple-pid", "from simple_pid import PID") == "simple_pid"


def test_an_uninstalled_distribution_falls_back_to_the_underscore_form():
    """No metadata and no matching import: the old behaviour must stand."""
    from smart_loader.core.ast_rewrite import resolve_import_root
    assert resolve_import_root(
        "not-installed-xyz", "") == "not_installed_xyz"


def test_the_post_condition_sees_a_surviving_statemachine_import():
    """The bug's actual consequence, end to end."""
    pytest = __import__("pytest")
    try:
        import statemachine                                   # noqa: F401
    except ImportError:
        pytest.skip("python-statemachine not installed")
    from smart_loader.core.migration_finalize import remaining_source_imports
    from smart_loader.core.ast_rewrite import resolve_import_root
    code = ("def build():\n"
            "    from statemachine import StateMachine\n"
            "    return StateMachine\n")
    root = resolve_import_root("python-statemachine", code)
    survivors = remaining_source_imports(code, root)
    assert survivors, "a live statemachine import was reported as absent"
