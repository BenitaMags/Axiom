"""The Connector must write against the INSTALLED library, not its memory of it.

B3 and D3 both died this way. The Connector was asked to write glue for
`amqtt` and given no information about the installed package at all, so it
invented::

    from amqtt.client import ClientException     # does not exist
    amqtt.Client(...)                            # does not exist

The real names are `amqtt.client.ClientError` and `amqtt.client.MQTTClient` --
one of them a single word from what the model wrote, both present in the
installed package. Nothing caught it before the edit: the project's own tests
inject a fake shaped like paho, so they pass; the failure surfaces as
`ImportError: cannot import name 'ClientException'` when layer 6 loads the real
library, and by then the Verifier reverts the whole migration. Three of twelve
proteus_full runs ended `migration_success=N` this way.

The package is installed, so this is answerable by import in milliseconds and
needs no model. Two halves, both from the literature:

  GROUNDING     the real surface goes INTO the prompt, so the names are in
                front of the model before it writes (De-Hallucinator,
                arXiv:2401.01701; MARIN constrains decoding to valid APIs).
  VALIDATION    what the model wrote is checked back against the package, and
                a miss is reported WITH the closest real names. "X does not
                exist" sends the model looking for another spelling of X;
                "X does not exist, did you mean ClientError?" hands it the
                answer. Reporting similar names is the part De-Hallucinator
                measures as doing the work.

What must never happen: a module that cannot be imported is not evidence. An
unverifiable symbol must not be called invented.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from smart_loader.agents.connector_agent import (  # noqa: E402
    API_SURFACE_BLOCK, _format_api_surface, _referenced_target_symbols,
    _winner_import_root, hallucinated_target_symbols, target_api_surface)

FAKE_SURFACE = {
    "amqtt": [],
    "amqtt.client": ["MQTTClient", "ClientError", "ClientConfig", "ConnectError"],
    "amqtt.contexts": ["ClientConfig", "BaseContext"],
}


def test_a_distribution_name_becomes_a_module_name():
    assert _winner_import_root("modbus-tk") == "modbus_tk"
    assert _winner_import_root("amqtt") == "amqtt"
    assert _winner_import_root("httpx[http2]") == "httpx"


# ------------------------------------------------- what the patch references ---

def test_from_imports_and_attribute_access_are_both_collected():
    src = ("import amqtt\n"
           "from amqtt.client import MQTTClient, ClientError\n"
           "def f():\n"
           "    return amqtt.Client()\n")
    found = _referenced_target_symbols(src, "amqtt")
    assert ("amqtt.client", "MQTTClient") in found
    assert ("amqtt.client", "ClientError") in found
    assert ("amqtt", "Client") in found


def test_an_unrelated_library_is_not_collected():
    src = "import json\nfrom os import path\n"
    assert _referenced_target_symbols(src, "amqtt") == []


def test_unparseable_source_is_not_guessed_at():
    assert _referenced_target_symbols("def broken(:", "amqtt") == []


# ------------------------------------------------------ the measured defects ---

def test_the_b3_hallucination_is_flagged_with_the_real_name():
    found = hallucinated_target_symbols(
        "from amqtt.client import ClientException\n", "amqtt", FAKE_SURFACE)
    assert len(found) == 1
    assert "ClientException` does not exist" in found[0]
    assert "amqtt.client.ClientError" in found[0], "the fix must be named"


def test_a_namespace_package_miss_is_answered_from_a_submodule():
    """`amqtt` has no public names of its own; the answer is one level down."""
    found = hallucinated_target_symbols(
        "import amqtt\ndef f(): return amqtt.Client()\n", "amqtt", FAKE_SURFACE)
    assert len(found) == 1
    assert "amqtt.client.MQTTClient" in found[0]


def test_correct_code_is_not_flagged():
    src = ("from amqtt.client import MQTTClient, ClientError\n"
           "def f(): return MQTTClient()\n")
    assert hallucinated_target_symbols(src, "amqtt", FAKE_SURFACE) == []


# ------------------------------------------------------------ the safety rule ---

def test_an_unimportable_module_is_never_called_invented():
    """Could-not-check must not be reported as does-not-exist."""
    src = "from nosuchlib.sub import Whatever\n"
    assert hallucinated_target_symbols(src, "nosuchlib", {}) == []
    # and a module absent from the surface is skipped even when others are present
    assert hallucinated_target_symbols(
        "from amqtt.broker import Broker\n", "amqtt", FAKE_SURFACE) == []


def test_an_empty_surface_disables_the_check_entirely():
    assert hallucinated_target_symbols(
        "from amqtt.client import Anything\n", "amqtt", {}) == []


# ------------------------------------------------------------------ grounding ---

def test_grounding_is_selective_not_a_dump():
    """De-Hallucinator ranks by the model's own prediction, not alphabetically.

    The first version listed every module alphabetically: 14 modules and 283
    names for `amqtt`, led by `amqtt.adapters`, with `amqtt.client` -- the only
    one that matters, holding BOTH names the model got wrong -- buried in the
    middle. Burying the answer in noise is the failure the grounding exists to
    prevent.
    """
    surface = target_api_surface(["amqtt"])
    if "amqtt.client" not in surface:
        pytest.skip("amqtt not installed here")
    prediction = ("L11: client = mqtt.Client(client_id=cfg.client_id) -> wrapper "
                  "_amqtt_sync_client_wrapper: build an amqtt MQTTClient, "
                  "connect, publish")
    text = _format_api_surface(surface, relevant_to=prediction)
    assert text.lstrip().startswith("amqtt.client"), "the relevant module leads"
    head = text[:150]
    assert "MQTTClient" in head and "ClientError" in head, \
        "both names the model got wrong must be visible immediately"


def test_the_package_name_cannot_discriminate_between_its_own_submodules():
    """`amqtt` yields the token `mqtt`, which matched `amqtt.mqtt`.

    That packet-encoding module outranked `amqtt.client` on raw symbol count,
    because every candidate shares the package name and it therefore carries no
    information about which submodule is wanted. Filtering on equality was not
    enough -- `_parts("amqtt")` never separates `mqtt` -- so the filter is by
    substring.
    """
    surface = {"amqtt": [], "amqtt.client": ["MQTTClient", "ClientError"],
               "amqtt.mqtt": ["connect", "publish", "CONNECT", "PUBLISH",
                              "ConnectPacket", "PublishPacket"]}
    text = _format_api_surface(surface, relevant_to="MQTTClient client_id mqtt")
    assert text.lstrip().startswith("amqtt.client")


def test_ranking_without_a_prediction_still_renders():
    assert _format_api_surface(FAKE_SURFACE, relevant_to="")


def test_the_surface_renders_for_a_prompt():
    text = _format_api_surface(FAKE_SURFACE)
    assert "amqtt.client" in text and "MQTTClient" in text
    assert "{" not in API_SURFACE_BLOCK.format(surface=text)


def test_an_empty_surface_renders_to_nothing():
    """No probe result means no block -- never an empty promise in the prompt."""
    assert _format_api_surface({}) == ""


def test_long_module_surfaces_are_truncated_with_a_count():
    text = _format_api_surface({"m": [f"n{i}" for i in range(60)]}, per_module=10)
    assert "(+50 more)" in text


# ------------------------------------------- against the really installed lib ---

@pytest.mark.parametrize("module,symbol,exists", [
    ("amqtt.client", "MQTTClient", True),
    ("amqtt.client", "ClientException", False),
])
def test_against_the_actual_installed_package(module, symbol, exists):
    surface = target_api_surface([module.split(".")[0]])
    if module not in surface:
        pytest.skip(f"{module} not installed here")
    assert (symbol in surface[module]) is exists
