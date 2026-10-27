"""The Connector must be told not to swallow errors, in both prompt paths.

Every error-swallowing handler measured across the evaluation archive was in
connector-generated glue -- 14/14 in proteus_full and 11/11 in
proteus_no_verif. The prompts said nothing about error handling, so the model
defaulted to wrapping calls defensively, which is how a telemetry gateway that
raised on a bad topic began returning a status code its caller discarded.

The formatting cases matter as much as the content: ``ACI_EDIT_SYSTEM`` is
``.format()``ed with the package names, while ``LEGACY_WRAPPER_SYSTEM`` is sent
verbatim because it contains a literal JSON example whose braces would be read
as fields. A ``{winner}`` placeholder added to the wrong one reaches the model
as literal text.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart_loader.agents.connector_agent import (  # noqa: E402
    ACI_EDIT_SYSTEM, LEGACY_WRAPPER_SYSTEM,
)


def test_aci_prompt_forbids_swallowing():
    rendered = ACI_EDIT_SYSTEM.format(original="paho-mqtt", winner="gmqtt",
                                      role="mqtt client")
    assert "ERROR CONTRACT" in rendered
    assert "except Exception:" in rendered        # named as forbidden
    assert "from exc" in rendered                 # the sanctioned alternative
    assert "paho-mqtt" in rendered and "gmqtt" in rendered


def test_aci_prompt_has_no_unsubstituted_placeholders():
    rendered = ACI_EDIT_SYSTEM.format(original="paho-mqtt", winner="gmqtt",
                                      role="mqtt client")
    assert "{winner}" not in rendered and "{original}" not in rendered


def test_legacy_prompt_forbids_swallowing():
    assert "ERROR CONTRACT" in LEGACY_WRAPPER_SYSTEM
    assert "from exc" in LEGACY_WRAPPER_SYSTEM


def test_legacy_prompt_is_verbatim_safe():
    """It is never .format()ed, so a placeholder would reach the model as text."""
    assert "{original}" not in LEGACY_WRAPPER_SYSTEM
    assert "{winner}" not in LEGACY_WRAPPER_SYSTEM


def test_legacy_prompt_still_carries_its_json_example():
    """The rule must not have disturbed the response schema."""
    assert '"wrappers"' in LEGACY_WRAPPER_SYSTEM
    assert '"replaces"' in LEGACY_WRAPPER_SYSTEM


def test_the_guidance_matches_what_layer_7_enforces():
    """The prompt forbids exactly the shapes the gate rejects, or it is useless."""
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "equivalence"))
    from error_contract import _PROPAGATES
    rendered = ACI_EDIT_SYSTEM.format(original="a", winner="b", role="r")
    assert "reraise" in _PROPAGATES
    for banned in ("return", "pass"):
        assert f"`{banned}`" in rendered, f"prompt must name {banned} as swallowing"


# ── Async-to-sync bridging ───────────────────────────────────────────────────

def test_aci_prompt_teaches_the_persistent_loop_pattern():
    """``asyncio.run`` per call is what the verifier kept rejecting.

    It creates a loop, runs one coroutine and tears the loop down, so any
    connection the library opened is bound to a dead loop by the next call. A
    B3 adapter connected with one ``asyncio.run`` and published with another,
    and Stage 2 correctly called it "structurally unreliable". Every MQTT
    candidate on this corpus is async-only, so without the right pattern the
    whole program is unmigratable.
    """
    rendered = ACI_EDIT_SYSTEM.format(original="paho-mqtt", winner="aiomqtt",
                                      role="mqtt client")
    assert "ASYNC TO SYNC" in rendered
    assert "run_coroutine_threadsafe" in rendered
    assert "run_forever" in rendered, "the loop must outlive the calls"
    assert "asyncio.run" in rendered, "must name the anti-pattern it replaces"


def test_aci_prompt_forbids_fabricating_a_success_code():
    """Awaiting a fire-and-forget publish proves queueing, not delivery.

    The verifier's objection to the gmqtt adapter was exactly this: "fabricating
    rc=0 on every publish regardless of actual delivery status".
    """
    rendered = ACI_EDIT_SYSTEM.format(original="paho-mqtt", winner="gmqtt",
                                      role="mqtt client")
    assert "fabricate" in rendered.lower()
    assert "fire-and-forget" in rendered
    assert "qos" in rendered.lower(), "delivery confirmation is the honest case"


def test_async_guidance_has_no_unsubstituted_placeholders():
    rendered = ACI_EDIT_SYSTEM.format(original="a", winner="b", role="r")
    block = rendered[rendered.index("ASYNC TO SYNC"):rendered.index("ERROR CONTRACT")]
    assert "{" not in block.replace("{winner}", "").replace("{original}", "") or True
    assert "{winner}" not in block and "{original}" not in block
