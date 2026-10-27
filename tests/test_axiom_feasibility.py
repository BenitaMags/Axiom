"""Focused guards for AXIOM's evidence-bounded feasibility selection."""

from __future__ import annotations

import json
import unittest

from smart_loader.agents.axiom_agent import _compute_scores, axiom_agent
from smart_loader.core.state import BenchmarkResult, EquivalenceGroup


class AxiomFeasibilityTests(unittest.TestCase):
    def _group(self, *candidates: str) -> EquivalenceGroup:
        return EquivalenceGroup(
            role="legacy migration",
            candidates=list(candidates),
            used_apis=[],
            reasoning="test fixture",
        )

    @staticmethod
    def _measured(package: str) -> BenchmarkResult:
        return BenchmarkResult(
            package=package,
            import_time_ms=1.5,
            memory_kb=10.0,
            available=True,
            profile_status="measured",
        )

    @staticmethod
    def _unmeasured(package: str) -> BenchmarkResult:
        return BenchmarkResult(
            package=package,
            import_time_ms=0.0,
            memory_kb=0.0,
            available=False,
            profile_status="neutral_unattempted",
        )

    def test_feasibility_first_requires_an_isolated_opt_in(self) -> None:
        group = self._group("legacy", "target")
        benchmarks = {
            "legacy": self._unmeasured("legacy"),
            "target": self._measured("target"),
        }

        host_winner, _confidence, host_scores, _crypto, _unmeasured = _compute_scores(
            group, benchmarks, {}, "test", "test", allow_profile_feasibility=False,
        )
        isolated_winner, isolated_confidence, isolated_scores, _crypto, _unmeasured = _compute_scores(
            group, benchmarks, {}, "test", "test", allow_profile_feasibility=True,
        )

        self.assertEqual(host_winner, "legacy")
        self.assertEqual(host_scores["legacy"]["selection_basis"], "insufficient_comparative_evidence")
        self.assertEqual(isolated_winner, "target")
        self.assertEqual(isolated_confidence, "LOW")
        self.assertEqual(isolated_scores["target"]["selection_basis"], "isolated_import_feasibility")
        self.assertTrue(isolated_scores["target"]["feasibility_selected"])

    def test_feasibility_first_never_breaks_a_tie_between_multiple_targets(self) -> None:
        group = self._group("legacy", "target-a", "target-b")
        benchmarks = {
            "legacy": self._unmeasured("legacy"),
            "target-a": self._measured("target-a"),
            "target-b": self._measured("target-b"),
        }

        winner, confidence, scores, _crypto, _unmeasured = _compute_scores(
            group, benchmarks, {}, "test", "test", allow_profile_feasibility=True,
        )

        self.assertEqual(winner, "legacy")
        self.assertEqual(confidence, "LOW")
        self.assertEqual(scores["legacy"]["selection_basis"], "insufficient_comparative_evidence")

    def test_axiom_records_serializable_selection_and_decline_evidence(self) -> None:
        group = self._group("legacy", "target")
        state = {
            "equivalence_groups": [group],
            "benchmarks": {
                "legacy": self._unmeasured("legacy"),
                "target": self._measured("target"),
            },
            "security_results": {},
            "source_code": "import legacy\n",
            "source_file": "fixture.py",
            "llm_provider": "test",
            "model": "test",
            "agent_trace": [],
            "isolated_profiling": True,
        }

        result = axiom_agent(state)

        self.assertEqual(result["decisions"][0].winner, "target")
        self.assertEqual(result["decision_evidence"][0]["selection_basis"], "isolated_import_feasibility")
        self.assertEqual(result["decision_evidence"][0]["outcome"], "selected_alternative")
        self.assertEqual(result["decision_declines"]["legacy migration"], [])
        json.dumps(result["decision_evidence"])
        json.dumps(result["decision_declines"])

    def test_feasibility_first_does_not_use_a_partial_base_environment(self) -> None:
        group = self._group("legacy", "target")
        state = {
            "equivalence_groups": [group],
            "benchmarks": {
                "legacy": self._unmeasured("legacy"),
                "target": self._measured("target"),
            },
            "base_environment_failures": {"requirements.txt": "legacy pin could not install"},
            "security_results": {},
            "source_code": "import legacy\n",
            "source_file": "fixture.py",
            "llm_provider": "test",
            "model": "test",
            "agent_trace": [],
            "isolated_profiling": True,
        }

        result = axiom_agent(state)

        self.assertEqual(result["decisions"][0].winner, "legacy")
        self.assertEqual(result["decision_evidence"][0]["selection_basis"], "insufficient_comparative_evidence")
        self.assertEqual(result["decision_declines"]["legacy migration"][0]["code"], "base_environment_incomplete")

    def test_hard_profile_failure_is_explained_as_a_decline(self) -> None:
        group = self._group("legacy", "broken-target")
        state = {
            "equivalence_groups": [group],
            "benchmarks": {
                "legacy": self._measured("legacy"),
                "broken-target": BenchmarkResult(
                    package="broken-target",
                    import_time_ms=0.0,
                    memory_kb=0.0,
                    available=False,
                    error="pip install failed",
                    profile_status="hard_install_failure",
                ),
            },
            "security_results": {},
            "source_code": "import legacy\n",
            "source_file": "fixture.py",
            "llm_provider": "test",
            "model": "test",
            "agent_trace": [],
            "isolated_profiling": True,
        }

        result = axiom_agent(state)

        self.assertEqual(result["decisions"][0].winner, "legacy")
        decline = result["decision_declines"]["legacy migration"][0]
        self.assertEqual(decline["candidate"], "broken-target")
        self.assertEqual(decline["code"], "hard_profile_failure")
        json.dumps(result["decision_evidence"])


if __name__ == "__main__":
    unittest.main()
