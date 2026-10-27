"""A Verifier revert must not be overridden by the isomorph variant.

The Logic Substitution Agent runs BEFORE the Verifier, so
``isomorph_variant_code`` is a diversified copy of the patch as it stood
before verification. When the Verifier exhausts its repair budget it reverts by
setting ``patched_code`` back to the original source -- and the runner used to
prefer the variant unconditionally, so the REJECTED patch shipped anyway,
wearing the LSA's renames.

D1 r1 is the case that exposed it. The Verifier detected ``Name "defines" is
not defined``, spent all five repair cycles on it, gave up, and reverted. The
runner shipped the variant of that rejected patch. The row scored
migration_success=Y on all seven layers, and every valve write in it raises
NameError -- confirmed by execution, and found by a blind LLM judge that all
seven layers had passed.

This is the most consequential shape of failure in the harness: the Verifier
does its job, records exactly why it refused, and the refusal is discarded.
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RUNNER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "run_axiom_arms.py")


def _source() -> str:
    return open(RUNNER, encoding="utf-8").read()


def test_the_pre_run_content_is_captured():
    """Recognising a revert needs what was on disk before the graph ran."""
    src = _source()
    assert "before_code = fh.read()" in src
    assert 'except OSError:\n                    before_code = ""' in src, \
        "an unreadable file must not raise; it must fall back"


def test_a_revert_discards_the_variant():
    src = _source()
    assert "reverted = patched.strip() and patched.strip() == before_code.strip()" in src
    block = src[src.index("reverted = patched.strip()"):]
    block = block[:block.index("shipped = variant if")]
    assert 'variant = ""' in block, "the variant must be cleared, not merely skipped"


def test_the_check_precedes_the_ship_decision():
    """Ordering is the whole bug: clearing after the choice changes nothing."""
    src = _source()
    assert src.index("reverted = patched.strip()") < src.index("shipped = variant if")


def test_capture_precedes_the_graph_call():
    src = _source()
    assert src.index("before_code = fh.read()") < src.index("state = run_loader(")


def test_an_accepted_patch_still_ships_its_variant():
    """The fix must not disable diversification on rows that were accepted.

    Only equality with the pre-run content marks a revert; a patch that differs
    from it was accepted and keeps its variant.
    """
    src = _source()
    block = src[src.index("reverted = patched.strip()"):]
    block = block[:block.index("shipped = variant if")]
    assert "if reverted and variant.strip():" in block, \
        "clearing must be conditional on the revert, not unconditional"


def test_the_runner_still_parses():
    ast.parse(_source())
