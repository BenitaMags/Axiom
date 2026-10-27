"""The in-graph Logic Substitution variant must reach the clone on disk.

`run_axiom_arms` wrote only `state["patched_code"]`, but the LSA returns its
result in `isomorph_variant_code` and leaves `patched_code` untouched. The CSV
read `isomorph_report` regardless, so a row could credit a transformation the
shipped clone did not contain. These tests pin the write-back rule.
"""
import ast
import os


def _write_back_source():
    """The exact source of the per-file write-back in run_axiom_arms."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return open(os.path.join(root, "run_axiom_arms.py"), encoding="utf-8").read()


def test_the_write_back_consults_the_isomorph_variant():
    """Scoped to the per-file write-back, not the whole module.

    `_diversify_remaining` further up reads `isomorph_variant_code` for the
    modules the graph never reached, so a file-wide substring check passes even
    when the in-graph write-back ignores the variant entirely.
    """
    src = _write_back_source()
    anchor = src.index('patched = state.get("patched_code")')
    region = src[max(0, anchor - 1200):anchor + 400]
    assert "isomorph_variant_code" in region, (
        "run_axiom_arms writes the clone without ever reading the Logic "
        "Substitution Agent's variant, so the in-graph variant is discarded "
        "while isomorph_report still credits it in the CSV."
    )


def _ship(state):
    """Mirror of the write-back decision, exercised over its inputs."""
    iso_applied = bool((state.get("isomorph_report") or {}).get("applied"))
    variant = state.get("isomorph_variant_code") or ""
    patched = state.get("patched_code") or ""
    return variant if (iso_applied and variant.strip()) else patched


def test_an_accepted_variant_supersedes_the_patch():
    assert _ship({
        "patched_code": "PATCH",
        "isomorph_variant_code": "VARIANT",
        "isomorph_report": {"applied": ["split_return_expression"]},
    }) == "VARIANT"


def test_a_rejected_variant_falls_back_to_the_patch():
    # applied == [] is how the agent reports "no transformation stuck".
    assert _ship({
        "patched_code": "PATCH",
        "isomorph_variant_code": "",
        "isomorph_report": {"applied": []},
    }) == "PATCH"


def test_no_isomorph_stage_at_all_falls_back_to_the_patch():
    assert _ship({"patched_code": "PATCH"}) == "PATCH"


def test_an_empty_variant_never_blanks_the_file():
    assert _ship({
        "patched_code": "PATCH",
        "isomorph_variant_code": "   \n",
        "isomorph_report": {"applied": ["expand_aug_assign"]},
    }) == "PATCH"


def test_the_decision_helper_matches_the_shipped_code():
    """Guard against the helper above drifting from run_axiom_arms itself."""
    src = _write_back_source()
    for fragment in (
        'iso_applied = bool((state.get("isomorph_report") or {})',
        'variant = state.get("isomorph_variant_code") or ""',
        'shipped = variant if (iso_applied and variant.strip()) else patched',
    ):
        assert fragment in src, f"write-back drifted: {fragment!r} not found"
    ast.parse(src)
