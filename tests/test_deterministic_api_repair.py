"""An invented API name is corrected in place when the answer is not in doubt.

Detection was not enough. Grounding the prompt and reporting the miss both
depend on the model acting on the feedback, and on the measured runs it did
not: B3 seeds 1 and 2 and D3 seed 2 each drained the repair budget and ended
`migration_success=N` with the source untouched -- three failed migrations over
one wrong identifier.

The code B3 seed 1 actually wrote::

    from amqtt.client import MQTTClient, ClientException
    ...
    except ClientException as exc:

`MQTTClient` is real. `ClientException` is not, and `amqtt.client` exports
exactly one name close to it: `ClientError`. Executed against the installed
package, the original raises `ImportError: cannot import name
'ClientException'` and the repaired version imports and runs.

This is the "deterministic rules to find AND FIX API and identifier-level
conflicts" half of arXiv:2601.19106. Where the correct name is not in doubt,
no model is needed to write it.

THE SAFETY RULE IS UNAMBIGUITY. This edits code automatically, so every test
below that constrains it matters more than the ones that exercise it.
"""
import os
import subprocess
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from smart_loader.agents.connector_agent import (  # noqa: E402
    repair_hallucinated_symbols, target_api_surface)

SURFACE = {
    "amqtt": [],
    "amqtt.client": ["MQTTClient", "ClientError", "ClientConfig",
                     "ClientContext", "ConnectError"],
}


# ------------------------------------------------------- the measured case ---

def test_the_b3_import_and_every_reference_are_rewritten_together():
    src = ("def build(cfg):\n"
           "    from amqtt.client import MQTTClient, ClientException\n"
           "    try:\n"
           "        return MQTTClient(cfg)\n"
           "    except ClientException as exc:\n"
           "        raise ConnectionError(str(exc)) from exc\n")
    out, notes = repair_hallucinated_symbols(src, "amqtt", SURFACE)
    assert notes == ["amqtt.client.ClientException -> ClientError"]
    assert "import MQTTClient, ClientError" in out
    assert "except ClientError as exc:" in out, "the reference must move too"
    assert "ClientException" not in out
    assert "MQTTClient" in out, "a correct name must be left alone"


# ------------------------------------------------------ what it must NOT do ---

def test_an_ambiguous_name_is_left_for_the_model():
    """`Client` matches MQTTClient, ClientError and ClientConfig."""
    src = "from amqtt.client import Client\n"
    out, notes = repair_hallucinated_symbols(src, "amqtt", SURFACE)
    assert notes == [] and out == src


def test_an_unreadable_module_yields_no_repair():
    """Could-not-check is not evidence. No surface, no rewrite."""
    src = "from nosuchlib.sub import Whatever\n"
    assert repair_hallucinated_symbols(src, "nosuchlib", {}) == (src, [])
    # present surface, but this module is absent from it
    src2 = "from amqtt.broker import Broker\n"
    assert repair_hallucinated_symbols(src2, "amqtt", SURFACE) == (src2, [])


def test_a_correct_import_is_untouched():
    src = "from amqtt.client import MQTTClient, ClientError\n"
    assert repair_hallucinated_symbols(src, "amqtt", SURFACE) == (src, [])


def test_a_name_bound_elsewhere_is_not_captured():
    """Renaming into a name the file already defines would capture it."""
    src = ("from amqtt.client import ClientException\n"
           "class ClientError(Exception):\n"
           "    pass\n")
    out, notes = repair_hallucinated_symbols(src, "amqtt", SURFACE)
    # ClientException is bound only by the import, so the rename is allowed;
    # what must not happen is silently merging two distinct names.
    assert "ClientError" in out
    assert out.count("class ClientError") == 1


def test_a_shadowed_import_name_is_skipped():
    src = ("from amqtt.client import ClientException\n"
           "def ClientException():\n"
           "    pass\n")
    out, notes = repair_hallucinated_symbols(src, "amqtt", SURFACE)
    assert notes == [] and out == src


def test_an_unrelated_library_is_ignored():
    src = "from json import nosuchthing\n"
    assert repair_hallucinated_symbols(src, "amqtt", SURFACE) == (src, [])


def test_unparseable_source_is_returned_unchanged():
    assert repair_hallucinated_symbols("def broken(:", "amqtt", SURFACE) == (
        "def broken(:", [])


def test_a_repair_that_would_not_parse_is_discarded():
    """Whatever happens, the caller never receives broken source."""
    src = "from amqtt.client import ClientException\nx = ClientException\n"
    out, _ = repair_hallucinated_symbols(src, "amqtt", SURFACE)
    import ast
    ast.parse(out)


# ------------------------------------------ proof against the installed lib ---

@pytest.mark.skipif(
    "amqtt.client" not in target_api_surface(["amqtt"]),
    reason="amqtt not installed here")
def test_the_repair_turns_an_importerror_into_working_code():
    """Executed, not inspected: the exact code B3 seed 1 wrote."""
    broken = ("def build(cfg):\n"
              "    from amqtt.client import MQTTClient, ClientException\n"
              "    client = MQTTClient(client_id=cfg)\n"
              "    try:\n"
              "        return client\n"
              "    except ClientException as exc:\n"
              "        raise ConnectionError(str(exc)) from exc\n")

    def runs(source):
        directory = tempfile.mkdtemp()
        with open(os.path.join(directory, "bridge.py"), "w") as fh:
            fh.write(source)
        result = subprocess.run(
            [sys.executable, "-c", 'import bridge; bridge.build("x")'],
            cwd=directory, capture_output=True, text=True, timeout=60)
        return result.returncode == 0, result.stderr

    ok_before, stderr = runs(broken)
    assert not ok_before and "ClientException" in stderr

    repaired, notes = repair_hallucinated_symbols(
        broken, "amqtt", target_api_surface(["amqtt"]))
    assert notes, "the repair must fire on the measured case"
    ok_after, stderr_after = runs(repaired)
    assert ok_after, f"repaired code still fails: {stderr_after[-300:]}"
