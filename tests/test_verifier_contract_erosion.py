"""The Verifier must check the Connector's output for contract erosion.

`smart_loader.isomorph.contract_check` has existed for a while and ran in
exactly one place: gating the Logic Substitution Agent's variants. The
Connector's output was never passed through it -- and the Connector erodes the
same contracts.

Measured on C3, all three seeds, in BOTH Proteus arms, every one rated MAJOR by
a blind judge for the same cause: the original imported smtplib lazily inside
`_default_smtp()` so importing notifier.py did not require it, and the patch
hoisted the replacement to module scope. The detector for that was sitting in
the repository while the defect shipped six times.

Replayed over the 144 archived clones, the check fires on 5/36 proteus_full
rows and 2/36 proteus_no_verif rows, and on ZERO of the 28 CLEAN rows across
all four arms -- every row it flags was independently rated BROKEN, MAJOR or
MINOR.

It is ADVISORY: it earns repair cycles but never reverts, because a contract
erosion means the migration is less faithful, not that it fails to run, and
reverting would turn a working migration into a no-op.

Unlike `error_contract` and B113 -- both recorded-never-repaired because their
implied fix is itself a behaviour change -- these three repairs only RESTORE
what the original had, so the fix cannot be the defect.
"""
import os
import sys
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from smart_loader.agents.verifier_agent import (  # noqa: E402
    _contract_erosion_errors)


def _s(code):
    return textwrap.dedent(code).lstrip()


ORIGINAL_LAZY = _s("""
    def _default_smtp(host="localhost", port=25):
        import smtplib          # lazy import for testability
        return smtplib.SMTP(host, port)
""")


# ------------------------------------------------------- the measured case ---

def test_a_hoisted_lazy_import_is_flagged():
    patched = _s("""
        import apprise

        def _default_smtp(host="localhost", port=25):
            return apprise.Apprise()
    """)
    found = _contract_erosion_errors(ORIGINAL_LAZY, patched)
    assert len(found) == 1
    assert "apprise" in found[0]["message"]
    assert found[0]["check"] == "contract_erosion"


def test_keeping_the_import_lazy_is_accepted():
    patched = _s("""
        def _default_smtp(host="localhost", port=25):
            import apprise      # lazy import for testability
            return apprise.Apprise()
    """)
    assert _contract_erosion_errors(ORIGINAL_LAZY, patched) == []


def test_a_lost_dataclass_is_flagged():
    original = _s("""
        from dataclasses import dataclass

        @dataclass
        class Process:
            level: float = 0.0
    """)
    patched = _s("""
        class Process:
            def __init__(self, level=0.0):
                self.level = level
    """)
    found = _contract_erosion_errors(original, patched)
    assert len(found) == 1 and "dataclass" in found[0]["message"]


def test_an_attribute_turned_read_only_is_flagged():
    original = _s("""
        class Controller:
            def __init__(self, kp):
                self.kp = kp
    """)
    patched = _s("""
        class Controller:
            def __init__(self, kp):
                self._kp = kp

            @property
            def kp(self):
                return self._kp
    """)
    found = _contract_erosion_errors(original, patched)
    assert len(found) == 1 and "kp" in found[0]["message"]


# ------------------------------------------------------------- quiet cases ---

def test_an_unchanged_patch_is_quiet():
    assert _contract_erosion_errors(ORIGINAL_LAZY, ORIGINAL_LAZY) == []


def test_an_empty_patch_is_quiet():
    assert _contract_erosion_errors(ORIGINAL_LAZY, "") == []


def test_unparseable_code_does_not_crash_the_verifier():
    assert _contract_erosion_errors(ORIGINAL_LAZY, "def broken(:") == []
    assert _contract_erosion_errors("def broken(:", ORIGINAL_LAZY) == []


def test_importing_a_sibling_module_is_not_erosion(tmp_path):
    """A first-party module is not a third-party collaborator."""
    (tmp_path / "estimator.py").write_text("X = 1\n")
    (tmp_path / "sink.py").write_text("\n")
    patched = _s("""
        from estimator import X

        def _default_smtp(host="localhost", port=25):
            import smtplib
            return smtplib.SMTP(host, port)
    """)
    found = _contract_erosion_errors(ORIGINAL_LAZY, patched,
                                     str(tmp_path / "sink.py"))
    assert found == []


def test_a_stdlib_import_is_not_erosion():
    patched = _s("""
        import os

        def _default_smtp(host="localhost", port=25):
            import smtplib
            return smtplib.SMTP(host, port)
    """)
    assert _contract_erosion_errors(ORIGINAL_LAZY, patched) == []


def test_contract_erosion_is_recorded_and_never_drives_a_repair():
    """Measured: repairing on it cost more migrations than it saved.

    C3, three seeds, against an archive where all three had migrated::

        gate (migration_success)   3/3 -> 1/3
        judge (CLEAN+MINOR)        0/3 -> 1/3
        iterations on run1           4 -> 10, then reverted at cycle 3/5

    The finding is correct and its repair is the right one -- the seed that
    finished kept its import lazy and went MAJOR -> MINOR. The damage was the
    repair CYCLES. An advisory finding never reverts by itself, but it spends
    the repair budget, and the churn pushed the Connector into code that
    tripped a blocking mypy check, which did revert. So the rule that governs
    `error_contract` and B113 governs this too, for a third distinct reason:
    not "the fix is the defect", but "the fix costs more than the defect".

    The guidance moved to the Connector's prompt instead, where it costs no
    cycles at all.
    """
    verifier = open(os.path.join(ROOT, "smart_loader/agents/verifier_agent.py"),
                    encoding="utf-8").read()
    start = verifier.index("advisory_errors: list[dict] =")
    initialiser = verifier[start:verifier.index("\n", verifier.index("=", start))]
    assert "_contract_erosion_errors" not in initialiser, \
        "contract erosion must not be fed into the repair-driving list"

    block = verifier[verifier.index("blocking_errors = ("):
                     verifier.index("deterministic_errors = blocking")]
    assert "contract_erosion" not in block, "it must never block or revert"
    assert 'check_type="contract_erosion"' in verifier, "it must still be recorded"


def test_the_connector_prompt_carries_the_deferred_import_rule():
    """The cycle-free path: tell the Connector before it writes, not after."""
    connector = open(os.path.join(ROOT, "smart_loader/agents/connector_agent.py"),
                     encoding="utf-8").read()
    assert connector.count("DEFERRED IMPORTS") == 2, \
        "both the ACI and the legacy prompt need the rule"
