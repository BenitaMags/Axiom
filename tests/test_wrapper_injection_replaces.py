"""A repair cycle must REPLACE the previous injection, not stack another copy.

Measured on D3 with ACI disabled, so every repair cycle took the one-shot path:
the file ended up holding both ``publish_via_aiomqtt_client`` and
``publish_via_aiomqtt_client_v2``. The stale one referenced ``asyncio`` and
``aiomqtt``, which the replacement imports only inside its own body, so it
produced `undefined-variable` findings -- which the verifier routed back as a
repair, which appended yet another wrapper. Five cycles, five wrappers, budget
exhausted, file reverted. A3 showed the same shape with five copies of
``parse_iso_datetime``.

Name matching cannot fix it: the model renames the replacement (``..._v2``), so
a name-based sweep leaves the stale definition in place. The block markers are
what make removal reliable.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart_loader.agents.connector_agent import (  # noqa: E402
    _BLOCK_END, _BLOCK_START, _strip_previous_injection,
)

BANNER = f"""

# {'─' * 70}
# {_BLOCK_START}: paho.mqtt.client → aiomqtt
# The following functions bridge API incompatibilities between
# paho.mqtt.client and aiomqtt. They are injected by the AXIOM
# connector agent and called in place of the original API.
# {'─' * 70}
"""

ORIGINAL = '''"""sink."""
import json


def publish(client, topic):
    return client.publish(topic)
'''


def _with_block(body: str, terminated: bool = True) -> str:
    head, _, tail = ORIGINAL.partition("import json\n")
    block = BANNER + body + (f"\n# {_BLOCK_END}\n" if terminated else "\n")
    return head + "import json\n" + block + tail


def test_a_terminated_block_is_removed():
    src = _with_block("def _wrapper(x):\n    return x\n")
    out = _strip_previous_injection(src)
    assert "_wrapper" not in out
    assert _BLOCK_START not in out
    assert "def publish(client, topic):" in out, "original code must survive"


def test_an_untagged_legacy_block_gives_up_only_its_banner():
    """There is no safe way to find where an unterminated block ends.

    Consuming the run of top-level definitions after the banner deleted the
    file's OWN next function, because only blank lines separated them. Losing
    a user's code to tidy a duplicate is worse than the duplicate.
    """
    src = _with_block("def _wrapper(x):\n    return x\n", terminated=False)
    out = _strip_previous_injection(src)
    assert _BLOCK_START not in out, "the banner goes"
    assert "def publish(client, topic):" in out, "the original code MUST survive"


def test_a_renamed_replacement_is_cleared_when_the_block_is_tagged():
    """The D3 shape: a ``_v2`` rename defeats any name-based sweep.

    Markers handle it; names cannot.
    """
    src = _with_block("def publish_via_aiomqtt_client(c, t, p):\n"
                      "    return asyncio.run(c.publish(t, p))\n")
    out = _strip_previous_injection(src)
    assert "publish_via_aiomqtt_client" not in out
    assert "def publish(client, topic):" in out


def test_source_with_no_injection_is_untouched():
    assert _strip_previous_injection(ORIGINAL) == ORIGINAL


def test_unparseable_source_is_returned_unchanged():
    broken = "def f(\n" + BANNER
    assert _strip_previous_injection(broken) == broken


def test_removal_that_would_break_the_file_is_discarded():
    """Shipping a duplicate beats shipping a syntax error."""
    out = _strip_previous_injection(ORIGINAL)
    assert "def publish" in out


def test_injector_strips_before_inserting():
    source = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "smart_loader", "agents", "connector_agent.py"),
                  encoding="utf-8").read()
    block = source[source.index("    if wrappers:"):]
    block = block[:block.index("valid, err = _validate_syntax(patched)")]
    assert "_strip_previous_injection(patched)" in block
    assert block.index("_strip_previous_injection") < block.index("lines.insert(")
    assert "_BLOCK_END" in block, "the new block must be terminated for next time"
