"""Regression tests for the evaluator/verifier false-success guards."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch

from smart_loader.agents.verifier_agent import (
    MAX_REPAIR_CYCLES,
    _reserve_repair_cycle,
    _new_static_failures,
    _measure_connector_adapter_runtime,
    _overlay_import_paths,
    _patch_integrity_errors,
    _run_import_smoke_test,
    _run_project_tests,
    _run_static_analysis,
    _rewritten_module_api_checks,
    _static_report_is_clean,
    _verification_environment,
    _verification_environment_cache_key,
    cleanup_verification_environment_cache,
    verifier_agent,
)
from smart_loader.agents.axiom_agent import _compute_scores
from smart_loader.agents.connector_agent import connector_agent, _connector_edit_candidate_violation
from smart_loader.core.ast_rewrite import rewrite_simple_attrs_to_dataclass
from smart_loader.agents.parser_agent import parser_agent
from smart_loader.agents.profiler_agent import (
    RUNTIME_PROBES,
    RUNTIME_SAMPLES,
    _attach_runtime_benchmarks,
    _base_specs_and_constraints,
    _normalize_historical_distribution_requirement,
    _normalize_legacy_requirement,
    _pinned_spec,
    _run_runtime_probe,
)
from smart_loader.agents.security_agent import (
    _FetchResult,
    SecurityResult,
    _advisory_severity,
    _compute_security_result,
    candidate_security_agent,
    security_agent,
)
from smart_loader.core.state import AgentEvent, BenchmarkResult, EquivalenceGroup, LoadDecision
from smart_loader.core.state import ImportInfo
from smart_loader.core.graph import (
    _after_axiom,
    _after_axiom_without_connector,
    _cleanup_disposable_environments,
)
from smart_loader.cli import _verify_combined_project_patches
from smart_loader.eval.pymigbench_eval import (
    EvalCase,
    EvalResult,
    _build_case_environment_plan,
    _compact_environment_plan,
    _environment_pipeline_inputs,
    _historical_project_snapshot,
    _import_name_matches,
    _requirements_at_pre_migration_commit,
    _alternative_efficiency_comparison,
    _result_category,
    _result_from_state,
    _source_import_for_case,
    compute_metrics,
    run_full_pipeline,
)


class PipelineGuardTests(unittest.TestCase):
    def _decision(self) -> LoadDecision:
        decision = LoadDecision(
            role="legacy -> target", winner="target", original="legacy",
            score={}, rationale="test",
        )
        decision.__dict__["original_import"] = "legacy"
        decision.__dict__["winner_import"] = "target"
        return decision

    def test_parser_attributes_from_imported_symbol_belong_to_the_package(self) -> None:
        """`from pkg import Symbol; Symbol.method()` keeps method evidence."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as source:
            source.write("from Crypto.Cipher import AES\nAES.new(b'0' * 16, 1)\n")
            source_path = source.name
        try:
            parsed = parser_agent({"source_file": source_path})
        finally:
            Path(source_path).unlink(missing_ok=True)
        imported = next(item for item in parsed["imports"] if item.module == "Crypto.Cipher")
        self.assertIn("new", imported.api_calls)

    def test_project_test_runner_uses_the_verification_interpreter_without_a_shell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.joinpath("check.py").write_text("assert 2 + 2 == 4\n", encoding="utf-8")
            report = _run_project_tests(
                "python check.py", python_executable=sys.executable, cwd=str(root),
                import_paths=[str(root)], timeout=10,
            )
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["command"][0], sys.executable)
            invalid = _run_project_tests(
                "python check.py && echo unsafe", python_executable=sys.executable,
                cwd=str(root), timeout=10,
            )
        self.assertEqual(invalid["status"], "invalid_command")

    def test_multi_file_save_requires_one_combined_project_test(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.py"
            second = root / "second.py"
            first.write_text("import legacy\n", encoding="utf-8")
            second.write_text("import legacy\n", encoding="utf-8")
            result = _verify_combined_project_patches(
                root,
                [(first, "import target\n"), (second, "import target\n")],
                plan={"requirements": [], "selected_python": sys.executable},
                source="legacy", target="target", target_versions=[{"target": "2.0.0"}],
                test_command="", test_timeout=5,
            )
        self.assertEqual(result["status"], "not_configured")
        self.assertIn("Refusing multi-file --save", result["output"])

    def test_verifier_environment_cache_key_includes_python_and_target_release(self) -> None:
        common = ("target", ["requests==2.31.0"], "legacy", ["target"])
        py38 = _verification_environment_cache_key(
            *common, python_executable="/opt/python3.8", target_versions={"target": "2.0.0"},
        )
        py312 = _verification_environment_cache_key(
            *common, python_executable="/opt/python3.12", target_versions={"target": "2.0.0"},
        )
        newer_target = _verification_environment_cache_key(
            *common, python_executable="/opt/python3.8", target_versions={"target": "2.1.0"},
        )
        self.assertNotEqual(py38, py312)
        self.assertNotEqual(py38, newer_target)

    def test_unresolved_target_is_environment_incomplete_even_when_axiom_keeps_source(self) -> None:
        decision = self._decision()
        decision.winner = decision.original
        result = verifier_agent({
            "decisions": [decision], "source_code": "import legacy\n", "patched_code": "import legacy\n",
            "target_resolution_failures": {"target": "no compatible release for Python 3.8"},
            "agent_trace": [], "verification_environment_cache": {},
        })
        self.assertEqual(result["verification_status"], "environment_incomplete")
        self.assertEqual(result["verification_environment_status"], "target_version_unresolved")

    def test_compact_environment_plan_reports_security_resolved_target_separately_from_auto_policy(self) -> None:
        compact = _compact_environment_plan({
            "target_distribution": "confluent-kafka",
            "target_version": "auto",
            "target_releases": {
                "confluent_kafka": {
                    "distribution": "confluent-kafka",
                    "version": "2.6.1",
                    "selection": "latest_compatible_non_yanked",
                },
            },
        })
        self.assertEqual(compact["target_version"], "auto")
        self.assertEqual(compact["resolved_target_version"], "2.6.1")

    def test_verifier_matches_target_resolution_failure_across_import_spelling(self) -> None:
        decision = self._decision()
        decision.winner = "target-package"
        result = verifier_agent({
            "decisions": [decision], "source_code": "import legacy\n", "patched_code": "import target_package\n",
            "target_resolution_failures": {"target_package": "no compatible release for Python 3.8"},
            "agent_trace": [], "verification_environment_cache": {},
        })
        self.assertEqual(result["verification_status"], "environment_incomplete")
        self.assertEqual(result["verification_environment_status"], "target_version_unresolved")

    def test_patch_integrity_rejects_empty_noop_and_deletion(self) -> None:
        source = "import legacy\n\ndef keep():\n    return 1\n"
        decision = self._decision()
        self.assertTrue(_patch_integrity_errors(source, "", [decision]))
        self.assertTrue(_patch_integrity_errors(source, source, [decision]))
        deleted = "import target\n"
        messages = [item["message"] for item in _patch_integrity_errors(source, deleted, [decision])]
        self.assertTrue(any("top-level definition 'keep'" in message for message in messages))

    def test_patch_integrity_rejects_deleted_module_binding_and_main_guard(self) -> None:
        source = (
            "import legacy\n"
            "SETTINGS = {'retries': 3}\n"
            "if __name__ == '__main__':\n"
            "    print(SETTINGS)\n"
        )
        patched = "import target\n"
        decision = self._decision()
        messages = [item["message"] for item in _patch_integrity_errors(source, patched, [decision])]
        self.assertTrue(any("top-level binding 'SETTINGS'" in message for message in messages))
        self.assertTrue(any("__main__ guard" in message for message in messages))

    def test_patch_integrity_accepts_a_real_import_migration(self) -> None:
        source = "import legacy\n\ndef keep():\n    return 1\n"
        patched = "import target\n\ndef keep():\n    return 1\n"
        self.assertEqual(_patch_integrity_errors(source, patched, [self._decision()]), [])

    def test_noop_patch_routes_connector_with_structured_repair_feedback(self) -> None:
        """A selected migration with no diff is repairable, not silently accepted."""
        source = "import legacy\n"
        state = {
            "decisions": [self._decision()],
            "patched_code": source,
            "source_code": source,
            "enable_connector": True,
            "_repair_cycle_count": 0,
            "_repair_history": [],
            "agent_trace": [],
            "llm_provider": "test",
            "model": "test",
        }

        result = verifier_agent(state)

        self.assertEqual(result["_verifier_route"], "connector")
        self.assertEqual(result["_repair_cycle_count"], 1)
        feedback = result["_last_verifier_feedback"]
        self.assertEqual(feedback["cycle"], 1)
        self.assertEqual(feedback["max_cycles"], MAX_REPAIR_CYCLES)
        self.assertEqual(feedback["route"], "connector")
        self.assertEqual(feedback["failure_class"], "patch_generation")
        self.assertEqual(feedback["roles"], ["legacy -> target"])
        self.assertIn("no source change", feedback["errors"][0]["message"])

    def test_connector_provider_failure_stops_without_spending_repair_budget(self) -> None:
        """Retrying an unavailable provider five times would not repair source code."""
        source = "import legacy\n"
        state = {
            "decisions": [self._decision()], "patched_code": source, "source_code": source,
            "connectors": {"legacy": {
                "failure_kind": "provider",
                "remaining_incompatibilities": ["NVIDIA rate limit exceeded"],
            }},
            "_repair_cycle_count": 0, "_repair_history": [], "agent_trace": [],
            "llm_provider": "test", "model": "test",
        }

        result = verifier_agent(state)

        self.assertEqual(result["_verifier_route"], "proceed")
        self.assertEqual(result["verification_status"], "not_run")
        self.assertEqual(result["verification_environment_status"], "provider_unavailable")
        self.assertIn("provider unavailable", result["error"].lower())
        self.assertNotIn("_repair_cycle_count", result)

    def test_connector_generation_failure_does_not_apply_an_import_only_patch(self) -> None:
        """A missing wrapper must remain a no-op so Verifier can retry Connector.

        Before this guard, a malformed Connector response was marked as a
        generation failure but the final import-rewrite pass still changed
        ``import legacy`` to ``import target``.  A target exposing similarly
        named symbols could then pass Stage 1 without the required wrapper.
        """
        source = "import legacy\nlegacy.do_work()\n"
        state = {
            "decisions": [self._decision()],
            "equivalence_groups": [EquivalenceGroup(
                role="legacy -> target", candidates=["legacy", "target"],
                used_apis=["legacy.do_work"], reasoning="test",
            )],
            "source_code": source,
            "patched_code": source,
            "llm_provider": "test",
            "model": "test",
            "agent_trace": [],
        }

        with patch("smart_loader.agents.connector_agent._get_llm", return_value=object()), \
             patch(
                 "smart_loader.agents.connector_agent.invoke_with_timeout",
                 return_value=SimpleNamespace(content="not valid connector JSON"),
             ):
            result = connector_agent(state)

        self.assertEqual(result["patched_code"], source)
        self.assertEqual(result["connectors"]["legacy"]["failure_kind"], "generation")
        self.assertIsNone(result["connectors"]["legacy"]["migration_feasible"])

        # With the source deliberately unchanged, Verifier now sees the real
        # generation failure as a repairable no-op and returns its concrete
        # feedback to Connector instead of accepting an import-only patch.
        verification_state = {
            **state,
            **result,
            "enable_connector": True,
            "_repair_cycle_count": 0,
            "_repair_history": [],
        }
        verification = verifier_agent(verification_state)
        self.assertEqual(verification["_verifier_route"], "connector")
        self.assertEqual(verification["_last_verifier_feedback"]["failure_class"], "patch_generation")

    def test_connector_rejects_analysis_about_an_unselected_candidate(self) -> None:
        """Compatibility evidence for pycodestyle cannot justify a ruff patch."""
        source = "import pep8\nif pep8.noqa(line):\n    pass\n"
        decision = LoadDecision(
            role="style checker", winner="ruff", original="pep8", score={}, rationale="test",
        )
        decision.__dict__["original_import"] = "pep8"
        decision.__dict__["winner_import"] = "ruff"
        state = {
            "decisions": [decision],
            "equivalence_groups": [EquivalenceGroup(
                role="style checker", candidates=["pep8", "pycodestyle", "ruff"],
                used_apis=["noqa"], reasoning="test",
            )],
            "source_code": source,
            "patched_code": source,
            "llm_provider": "test",
            "model": "test",
            "agent_trace": [],
        }
        response = {
            "analysis": [{
                "line": 2, "call": "pep8.noqa(line)", "compatible": True,
                "reason": "pycodestyle provides an identical noqa function",
                "wrapper_name": None, "wrapper_summary": "",
            }],
            "migration_feasible": True,
            "remaining_incompatibilities": [],
        }

        with patch("smart_loader.agents.connector_agent._get_llm", return_value=object()), \
             patch(
                 "smart_loader.agents.connector_agent.invoke_with_timeout",
                 return_value=SimpleNamespace(content=json.dumps(response)),
             ) as invoke:
            result = connector_agent(state)

        self.assertEqual(result["patched_code"], source)
        connector = result["connectors"]["pep8"]
        self.assertEqual(connector["failure_kind"], "candidate_identity")
        self.assertFalse(connector["migration_feasible"])
        self.assertIn("pycodestyle", connector["remaining_incompatibilities"][0])
        prompt = invoke.call_args.args[1][1].content
        self.assertIn("LOCKED TARGET DISTRIBUTION: ruff", prompt)
        self.assertIn("LOCKED TARGET IMPORT ROOT: ruff", prompt)

        verification = verifier_agent({
            **state, **result,
            "enable_connector": True,
            "_repair_cycle_count": 0,
            "_repair_history": [],
        })
        self.assertEqual(verification["_verifier_route"], "axiom")
        self.assertIn("ruff", verification["verification_failures"]["style checker"])

    def test_connector_rejects_aci_edit_that_introduces_an_unselected_candidate(self) -> None:
        before = "import pep8\n\ndef check(line):\n    return pep8.noqa(line)\n"
        after = (
            "import pycodestyle\n\n"
            "def _ruff_noqa_wrapper(line):\n"
            "    return pycodestyle.noqa(line)\n\n"
            "def check(line):\n"
            "    return _ruff_noqa_wrapper(line)\n"
        )
        error = _connector_edit_candidate_violation(
            before, after, original_import="pep8", foreign_candidates=["pycodestyle", "flake8"],
        )
        self.assertIsNotNone(error)
        self.assertIn("source import", error or "")

    def test_simple_attrs_dataclass_recipe_uses_native_class_body_syntax(self) -> None:
        source = (
            "import attr\n\n"
            "@attr.s\n"
            "class CandidateConstructionFailure(Exception):\n"
            "    message: str = attr.ib()\n"
        )
        rewritten = rewrite_simple_attrs_to_dataclass(source)
        self.assertIsNotNone(rewritten)
        assert rewritten is not None
        code, edits = rewritten
        self.assertIn("from dataclasses import dataclass", code)
        self.assertIn("@dataclass", code)
        self.assertIn("message: str", code)
        self.assertNotIn("attr.ib", code)
        self.assertEqual(len(edits), 3)

        namespace: dict[str, object] = {}
        exec(code, namespace)
        error = namespace["CandidateConstructionFailure"]("expected")
        self.assertEqual(error.message, "expected")

    def test_simple_attrs_dataclass_recipe_declines_complex_attrs_features(self) -> None:
        source = (
            "import attr\n\n"
            "@attr.s\n"
            "class Record:\n"
            "    value: int = attr.ib(default=1)\n"
        )
        self.assertIsNone(rewrite_simple_attrs_to_dataclass(source))

        mixed_use = (
            "import attr\n\n"
            "saved_attr_module = attr\n\n"
            "@attr.s\n"
            "class Record:\n"
            "    value: int = attr.ib()\n"
        )
        self.assertIsNone(rewrite_simple_attrs_to_dataclass(mixed_use))

    def test_connector_applies_simple_attrs_recipe_without_calling_llm(self) -> None:
        source = (
            "import attr\n\n"
            "@attr.s\n"
            "class CandidateConstructionFailure(Exception):\n"
            "    message: str = attr.ib()\n"
        )
        decision = LoadDecision(
            role="record construction", original="attrs", winner="dataclasses",
            score={}, rationale="test",
        )
        decision.__dict__["original_import"] = "attr"
        decision.__dict__["winner_import"] = "dataclasses"
        state = {
            "decisions": [decision],
            "equivalence_groups": [EquivalenceGroup(
                role="record construction", candidates=["attrs", "dataclasses"],
                used_apis=["attr.s", "attr.ib"], reasoning="test",
            )],
            "source_code": source, "patched_code": source,
            "llm_provider": "test", "model": "test", "agent_trace": [],
        }
        with patch("smart_loader.agents.connector_agent._get_llm") as get_llm:
            result = connector_agent(state)
        get_llm.assert_not_called()
        self.assertIn("from dataclasses import dataclass", result["patched_code"])
        self.assertIn("@dataclass", result["patched_code"])
        self.assertNotIn("attr.ib", result["patched_code"])
        connector = result["connectors"]["attrs"]
        self.assertTrue(connector["compatible"])
        self.assertEqual(connector["deterministic_rewrite"], "simple_attrs_dataclass")

    def test_publication_categories_do_not_call_an_unproven_alternative_better(self) -> None:
        category, reason = _result_category(
            "verified_alternative",
            alternative_efficiency={"verdict": "unavailable"},
            repair_budget_exhausted=False,
        )
        self.assertEqual(category, "verified_alternative")
        self.assertIn("no runtime-efficiency claim", reason)

        category, _ = _result_category(
            "verification_rejected",
            alternative_efficiency={},
            repair_budget_exhausted=True,
        )
        self.assertEqual(category, "rejected_after_repair_budget")

    def test_alternative_efficiency_requires_real_comparable_measurements(self) -> None:
        case = EvalCase(
            migration_id="legacy__reference__owner/repo__deadbeef",
            repo="owner/repo", domain="Utility", source="legacy", target="reference",
            file_path="module.py", before_code="import legacy\n",
        )
        evidence = {
            "reference": {
                "available": True, "import_time_ms": 10.0, "memory_kb": 1000.0,
                "runtime": {"measured": False}, "runtime_probes": {},
            },
            "alternative": {
                "available": True, "import_time_ms": 8.0, "memory_kb": 800.0,
                "runtime": {"measured": False}, "runtime_probes": {},
            },
        }
        comparison = _alternative_efficiency_comparison(case, "alternative", evidence)
        self.assertEqual(comparison["verdict"], "import_memory_only")
        self.assertFalse(comparison["eligible"])
        self.assertIn("does not invoke", comparison["reason"])

    def test_runtime_efficiency_requires_target_api_use_and_shared_semantic_probe(self) -> None:
        case = EvalCase(
            migration_id="legacy__reference__owner/repo__deadbeef",
            repo="owner/repo", domain="Utility", source="legacy", target="reference",
            file_path="module.py", before_code="import legacy\n",
        )
        decision = LoadDecision(
            role="legacy -> alternative", original="legacy", winner="alternative", score={}, rationale="test",
        )
        decision.__dict__["original_import"] = "legacy"
        decision.__dict__["winner_import"] = "alternative"

        def profile(runtime_ms: float) -> dict:
            return {
                "available": True, "import_time_ms": 10.0, "memory_kb": 1000.0,
                "runtime": {"measured": True},
                "runtime_probes": {
                    "shared_api": {"status": "measured", "median_ms": runtime_ms},
                },
            }

        evidence = {"legacy": profile(10.0), "reference": profile(12.0), "alternative": profile(8.0)}
        comparison = _alternative_efficiency_comparison(
            case, "alternative", evidence,
            patched_code="import alternative\nalternative.do_work()\n",
            selected_decision=decision,
        )
        self.assertEqual(comparison["verdict"], "runtime_efficiency_superior")
        self.assertTrue(comparison["target_api_usage"]["invoked"])
        self.assertEqual(comparison["runtime_evidence"]["all_three_shared_probe"], "shared_api")

    def test_pure_regex_connector_wrapper_is_measured_as_adapter_only(self) -> None:
        decision = LoadDecision(
            role="pep8 -> ruff", original="pep8", winner="ruff", score={}, rationale="test",
        )
        source = (
            "import re\n\n"
            "def _ruff_noqa_wrapper(physical_line):\n"
            "    \"\"\"Compatibility adapter.\"\"\"\n"
            "    return bool(re.search('#\\\\s*noqa\\\\b', physical_line))\n"
        )
        records = _measure_connector_adapter_runtime(
            source,
            {"pep8": {"functions": ["_ruff_noqa_wrapper"]}},
            [decision],
            python_executable=sys.executable,
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["scope"], "adapter_only")
        self.assertEqual(records[0]["status"], "measured")
        self.assertGreater(records[0]["median_ms"], 0.0)

    def test_result_keeps_connector_telemetry_after_an_axiom_retry_clears_connectors(self) -> None:
        case = EvalCase(
            migration_id="legacy__target__owner/repo__deadbeef",
            repo="owner/repo", domain="Utility", source="legacy", target="target",
            file_path="module.py", before_code="import legacy\n",
        )
        decision = self._decision()
        result = _result_from_state(
            case,
            {
                "decisions": [decision], "attempted_decision_history": [[decision]],
                "source_code": case.before_code, "patched_code": case.before_code,
                "connectors": {}, "agent_trace": [AgentEvent(stage="connector", event="started")],
                "verification_status": "not_applicable", "verification_environment_status": "ready",
            },
            detected_group=True,
        )
        self.assertTrue(result.connector_ran)
        self.assertTrue(result.connector_ever_ran)

    def test_no_connector_graph_never_receives_an_unregistered_connector_route(self) -> None:
        """Verifier must re-score an unrewritable selection when Connector is off."""
        source = "import legacy\n"
        state = {
            "decisions": [self._decision()], "patched_code": source, "source_code": source,
            "enable_connector": False, "_repair_cycle_count": 0, "_repair_history": [],
            "agent_trace": [], "llm_provider": "test", "model": "test",
        }

        result = verifier_agent(state)

        self.assertEqual(result["_verifier_route"], "axiom")
        self.assertEqual(result["_last_verifier_feedback"]["route"], "axiom")
        self.assertEqual(result["_repair_cycle_count"], 1)

    def test_repair_budget_is_global_across_axiom_and_connector_routes(self) -> None:
        """Five total repairs is a global cap, not five retries per agent."""
        state = {"_repair_cycle_count": 0, "_repair_history": []}
        trace = []
        routes = ["connector", "axiom", "connector", "axiom", "connector"]

        for expected_cycle, route in enumerate(routes, start=1):
            feedback, history = _reserve_repair_cycle(
                state,
                trace,
                route=route,
                failure_class="test_failure",
                roles=["legacy -> target"],
                errors=[{"message": f"failure {expected_cycle}"}],
            )
            self.assertIsNotNone(feedback)
            assert feedback is not None
            self.assertEqual(feedback["cycle"], expected_cycle)
            self.assertEqual(feedback["route"], route)
            state["_repair_cycle_count"] = feedback["cycle"]
            state["_repair_history"] = history

        self.assertEqual(state["_repair_cycle_count"], MAX_REPAIR_CYCLES)
        self.assertEqual(len(state["_repair_history"]), MAX_REPAIR_CYCLES)
        exhausted, history = _reserve_repair_cycle(
            state,
            trace,
            route="axiom",
            failure_class="test_failure",
            roles=["legacy -> target"],
        )
        self.assertIsNone(exhausted)
        self.assertEqual(len(history), MAX_REPAIR_CYCLES)

    def test_environment_block_does_not_consume_repair_budget(self) -> None:
        """Missing historical dependencies require environment repair, not an LLM loop."""
        source = "import legacy\n"
        state = {
            "decisions": [self._decision()],
            "patched_code": "import target\n",
            "source_code": source,
            "base_environment_failures": {"legacy-base": "wheel cannot be built"},
            "_repair_cycle_count": 3,
            "_repair_history": [{"cycle": 1}, {"cycle": 2}, {"cycle": 3}],
            "agent_trace": [],
            "llm_provider": "test",
            "model": "test",
        }

        result = verifier_agent(state)

        self.assertEqual(result["verification_status"], "environment_incomplete")
        self.assertEqual(result["verification_environment_status"], "blocked")
        self.assertEqual(result["_verifier_route"], "proceed")
        self.assertEqual(state["_repair_cycle_count"], 3)
        self.assertNotIn("_repair_cycle_count", result)
        self.assertFalse(any(event.event == "repair_routed" for event in result["agent_trace"]))

    def test_verifier_probes_alias_bound_api_symbols_after_module_rewrite(self) -> None:
        decision = self._decision()
        decision.__dict__["original_import"] = "legacy"
        decision.__dict__["winner_import"] = "target"
        source = "import legacy as client\nclient.LegacyProducer()\n"
        patched = "import target as client\nclient.LegacyProducer()\n"
        checks = _rewritten_module_api_checks(source, patched, [decision])
        self.assertEqual(checks, [{"module": "target", "symbol": "LegacyProducer", "line": 2}])

    def test_smoke_timeout_is_not_a_pass(self) -> None:
        passed, detail = _run_import_smoke_test("while True: pass\n", timeout=1)
        self.assertFalse(passed)
        self.assertIn("inconclusive", detail)

    def test_missing_static_tools_are_not_clean(self) -> None:
        unavailable = {
            "ran": False, "status": "unavailable", "returncode": 1,
            "stdout": "", "stderr": "No module named tool", "raw": "No module named tool",
        }
        with patch("smart_loader.agents.verifier_agent._run_tool", return_value=unavailable):
            report = _run_static_analysis("x = 1\n")
        self.assertFalse(report["toolchain_ready"])
        self.assertEqual(report["pylint"]["status"], "unavailable")
        self.assertEqual(report["bandit"]["status"], "unavailable")
        self.assertEqual(report["mypy"]["status"], "unavailable")

    def test_static_findings_are_not_hidden_by_nonzero_exit_codes(self) -> None:
        runs = iter([
            {
                "ran": True, "status": "ok", "returncode": 2,
                "stdout": '[{"line": 1, "symbol": "undefined-variable", "message": "x is undefined"}]',
                "stderr": "", "raw": "pylint findings",
            },
            {
                "ran": True, "status": "ok", "returncode": 0,
                "stdout": '{"results": []}', "stderr": "", "raw": "",
            },
            {
                "ran": True, "status": "ok", "returncode": 1,
                "stdout": 'sample.py:1: error: Name "x" is not defined', "stderr": "", "raw": "mypy findings",
            },
        ])
        with patch("smart_loader.agents.verifier_agent._run_tool", side_effect=lambda *args, **kwargs: next(runs)):
            report = _run_static_analysis("x\n")
        self.assertEqual(report["pylint"]["status"], "findings")
        self.assertEqual(report["mypy"]["status"], "findings")
        self.assertTrue(report["toolchain_ready"])
        self.assertFalse(_static_report_is_clean(report))

    def test_analyzer_invocation_errors_cannot_be_clean(self) -> None:
        runs = iter([
            {"ran": True, "status": "ok", "returncode": 0, "stdout": "[]", "stderr": "", "raw": ""},
            {"ran": True, "status": "ok", "returncode": 1, "stdout": '{"results": []}', "stderr": "", "raw": "bad invocation"},
            {"ran": True, "status": "ok", "returncode": 0, "stdout": "", "stderr": "", "raw": ""},
        ])
        with patch("smart_loader.agents.verifier_agent._run_tool", side_effect=lambda *args, **kwargs: next(runs)):
            report = _run_static_analysis("x = 1\n")
        self.assertEqual(report["bandit"]["status"], "tool_error")
        self.assertFalse(report["toolchain_ready"])

    def test_source_import_preserves_python_case(self) -> None:
        case = EvalCase(
            migration_id="pycrypto-test", repo="example/repo", domain="test",
            source="pycrypto", target="pycryptodome", file_path="client.py",
            before_code="from Crypto.Cipher import AES\n",
        )
        self.assertEqual(_source_import_for_case(case), "Crypto")
        self.assertTrue(_import_name_matches("Crypto", "pycrypto"))

    def test_resolver_marks_the_actual_imported_source_not_llm_order(self) -> None:
        from types import SimpleNamespace
        from smart_loader.agents.resolver_agent import resolver_agent

        class FakeLLM:
            def invoke(self, _messages):
                return SimpleNamespace(
                    content='[{"role":"Kafka client","candidates":["confluent-kafka","kafka"],'
                            '"used_apis":["KafkaProducer"],"reasoning":"test","pitfalls":[]}]',
                    usage_metadata={},
                )

        state = {
            "imports": [ImportInfo(module="kafka", alias=None, names=[], line=1, api_calls=["KafkaProducer"])],
            "rule_results": [], "security_results": {}, "source_code": "import kafka\n",
            "llm_provider": "test", "model": "test", "agent_trace": [],
            "migration_source": "kafka-python", "migration_source_import": "kafka",
        }
        with patch("smart_loader.agents.resolver_agent._get_llm", return_value=FakeLLM()), \
             patch("smart_loader.agents.resolver_agent._discover_alternatives", return_value={}):
            result = resolver_agent(state)
        group = result["equivalence_groups"][0]
        self.assertEqual(group.candidates[0], "kafka-python")
        self.assertEqual(getattr(group, "original"), "kafka-python")
        self.assertEqual(getattr(group, "candidate_imports")["kafka-python"], "kafka")

    def test_resolver_discovers_allowlisted_stdlib_cli_migrations(self) -> None:
        from types import SimpleNamespace
        from smart_loader.agents.resolver_agent import resolver_agent

        class FakeLLM:
            def invoke(self, _messages):
                return SimpleNamespace(
                    content='[{"role":"CLI","candidates":["argparse","click"],'
                            '"used_apis":["ArgumentParser"],"reasoning":"test","pitfalls":[]}]',
                    usage_metadata={},
                )

        state = {
            "imports": [ImportInfo(module="argparse", alias=None, names=[], line=1, api_calls=["ArgumentParser"])],
            "rule_results": [], "security_results": {}, "source_code": "import argparse\n",
            "llm_provider": "test", "model": "test", "agent_trace": [],
            "migration_source": "argparse", "migration_source_import": "argparse",
        }
        with patch("smart_loader.agents.resolver_agent._get_llm", return_value=FakeLLM()), \
             patch("smart_loader.agents.resolver_agent._discover_alternatives", return_value={}) as discover:
            result = resolver_agent(state)
        self.assertEqual(result["equivalence_groups"][0].candidates[0], "argparse")
        self.assertEqual(discover.call_args.args[0], ["argparse"])

    def test_patch_integrity_allows_a_scoped_partial_migration(self) -> None:
        source = "from legacy import Keep, Move\n\ndef use():\n    return Move()\n"
        patched = "from legacy import Keep\nfrom target import Move\n\ndef use():\n    return Move()\n"
        self.assertEqual(_patch_integrity_errors(source, patched, [self._decision()]), [])

    def test_overlay_paths_include_src_package_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            module = root / "src" / "pkg" / "sub" / "mod.py"
            module.parent.mkdir(parents=True)
            (root / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
            (root / "src" / "pkg" / "sub" / "__init__.py").write_text("", encoding="utf-8")
            module.write_text("from . import sibling\n", encoding="utf-8")
            paths = _overlay_import_paths(str(root), str(module))
            self.assertIn(str(root / "src"), paths)

    def test_overlay_paths_do_not_shadow_installed_package_with_package_module(self) -> None:
        """A package's `kafka.py` must not shadow installed `kafka` imports."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = root / "locust_plugins"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            module = package / "kafka.py"
            module.write_text("from kafka import KafkaProducer\n", encoding="utf-8")
            paths = _overlay_import_paths(str(root), str(module))
            self.assertIn(str(root), paths)
            self.assertNotIn(str(package), paths)

    def test_overlay_paths_keep_standalone_module_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            module = root / "standalone.py"
            module.write_text("import sibling\n", encoding="utf-8")
            paths = _overlay_import_paths(str(root), str(module))
            self.assertIn(str(root), paths)

    def test_verification_subprocess_environment_does_not_leak_api_key(self) -> None:
        with patch.dict("os.environ", {"NVIDIA_API_KEY": "secret", "PATH": "/bin"}, clear=True):
            env = _verification_environment()
        self.assertNotIn("NVIDIA_API_KEY", env)
        self.assertEqual(env["PATH"], "/bin")

    def test_baseline_findings_only_fail_when_patch_adds_new_finding(self) -> None:
        baseline = {
            "syntax_ok": True,
            "pylint": {"errors": [{"symbol": "undefined-variable", "message": "old is undefined"}]},
            "bandit": {"issues": []}, "mypy": {"errors": []},
        }
        same = {
            "syntax_ok": True,
            "pylint": {"errors": [{"symbol": "undefined-variable", "message": "old is undefined"}]},
            "bandit": {"issues": []}, "mypy": {"errors": []},
        }
        worse = {
            "syntax_ok": True,
            "pylint": {"errors": [
                {"symbol": "undefined-variable", "message": "old is undefined"},
                {"symbol": "undefined-variable", "message": "new is undefined"},
            ]},
            "bandit": {"issues": []}, "mypy": {"errors": []},
        }
        self.assertEqual(_new_static_failures(baseline, same), [])
        self.assertEqual(len(_new_static_failures(baseline, worse)), 1)

    def test_verifier_stage1_accepts_only_no_regression_against_baseline(self) -> None:
        def static_report(with_existing_finding: bool) -> dict:
            return {
                "syntax_ok": True, "syntax_error": "", "toolchain_ready": True,
                "pylint": {
                    "ran": True, "status": "findings" if with_existing_finding else "ok",
                    "returncode": 2 if with_existing_finding else 0,
                    "errors": ([{"line": 1, "symbol": "undefined-variable", "message": "legacy debt"}]
                               if with_existing_finding else []), "raw": "",
                },
                "bandit": {"ran": True, "status": "ok", "returncode": 0, "issues": [], "raw": ""},
                "mypy": {"ran": True, "status": "ok", "returncode": 0, "errors": [], "raw": ""},
            }

        decision = self._decision()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_file = root / "client.py"
            source = "import legacy\n\ndef keep():\n    return 1\n"
            patched = "import target\n\ndef keep():\n    return 1\n"
            source_file.write_text(source, encoding="utf-8")
            env_dirs: list[str] = []

            def make_environment(*_args, **_kwargs):
                env_dir = tempfile.mkdtemp(prefix="axiom-verify-env-test-")
                env_dirs.append(env_dir)
                return env_dir, "python", {}, {"pylint": "available", "bandit": "available", "mypy": "available"}

            def make_tool_environment():
                env_dir = tempfile.mkdtemp(prefix="axiom-static-tools-test-")
                env_dirs.append(env_dir)
                return env_dir, "python", {"pylint": "available", "bandit": "available", "mypy": "available"}

            state = {
                "decisions": [decision], "connectors": {"legacy": {"migration_feasible": True}},
                "patched_code": patched, "source_code": source, "llm_provider": "test", "model": "test",
                "source_file": "client.py", "verification_project_root": str(root),
                "verification_file_path": "client.py", "isolated_profiling": True,
                "base_requirements": [], "migration_source": "legacy", "agent_trace": [],
                "verifier_results": [], "verification_failures": {},
            }
            with patch("smart_loader.agents.profiler_agent.create_verification_environment", side_effect=make_environment), \
                 patch("smart_loader.agents.profiler_agent.create_static_analysis_environment", side_effect=make_tool_environment), \
                 patch("smart_loader.agents.verifier_agent._run_static_analysis", side_effect=[static_report(True), static_report(True)]), \
                 patch("smart_loader.agents.verifier_agent._run_import_smoke_test", return_value=(True, "ok")), \
                 patch("smart_loader.agents.verifier_agent._save"):
                result = verifier_agent(state)
            self.assertEqual(result["verification_status"], "verified")
            self.assertEqual(result["verification_environment_status"], "ready")
            self.assertEqual(len(result["verification_environment_cache"]), 3)
            for env_dir in env_dirs:
                self.assertTrue(Path(env_dir).exists())
            cleanup_verification_environment_cache(result["verification_environment_cache"])
            for env_dir in env_dirs:
                self.assertFalse(Path(env_dir).exists())

    def test_verifier_reuses_successful_case_environments_across_repair(self) -> None:
        """A Connector retry must not reinstall baseline, target, or tools."""
        def report(with_error: bool = False) -> dict:
            return {
                "syntax_ok": True, "syntax_error": "", "toolchain_ready": True,
                "pylint": {
                    "ran": True, "status": "findings" if with_error else "ok",
                    "returncode": 2 if with_error else 0,
                    "errors": ([{"line": 1, "symbol": "undefined-variable", "message": "new patch error"}]
                               if with_error else []),
                    "raw": "",
                },
                "bandit": {"ran": True, "status": "ok", "returncode": 0, "issues": [], "raw": ""},
                "mypy": {"ran": True, "status": "ok", "returncode": 0, "errors": [], "raw": ""},
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "client.py").write_text("import legacy\n", encoding="utf-8")
            env_dirs: list[str] = []

            def make_environment(*_args, **_kwargs):
                env_dir = tempfile.mkdtemp(prefix="axiom-verify-env-cache-test-")
                env_dirs.append(env_dir)
                return env_dir, "python", {}, {}

            def make_tool_environment():
                env_dir = tempfile.mkdtemp(prefix="axiom-static-tools-cache-test-")
                env_dirs.append(env_dir)
                return env_dir, "python", {"pylint": "available", "bandit": "available", "mypy": "available"}

            state = {
                "decisions": [self._decision()],
                "connectors": {"legacy": {"migration_feasible": True}},
                "patched_code": "import target\n", "source_code": "import legacy\n",
                "llm_provider": "test", "model": "test", "source_file": "client.py",
                "verification_project_root": str(root), "verification_file_path": "client.py",
                "isolated_profiling": True, "base_requirements": [], "migration_source": "legacy",
                "agent_trace": [], "verifier_results": [], "verification_failures": {},
                "verification_environment_cache": {}, "enable_connector": True,
                "_repair_cycle_count": 0, "_repair_history": [],
            }
            with patch("smart_loader.agents.profiler_agent.create_verification_environment", side_effect=make_environment) as make_env, \
                 patch("smart_loader.agents.profiler_agent.create_static_analysis_environment", side_effect=make_tool_environment) as make_tools, \
                 patch("smart_loader.agents.verifier_agent._run_static_analysis", side_effect=[report(), report(True), report(), report()]), \
                 patch("smart_loader.agents.verifier_agent._run_import_smoke_test", return_value=(True, "ok")), \
                 patch("smart_loader.agents.verifier_agent._save"):
                first = verifier_agent(state)
                self.assertEqual(first["_verifier_route"], "connector")
                self.assertEqual(len(first["verification_environment_cache"]), 3)
                self.assertEqual(make_env.call_count, 2)
                self.assertEqual(make_tools.call_count, 1)
                self.assertTrue(all(Path(path).exists() for path in env_dirs))

                state.update(first)
                second = verifier_agent(state)

            self.assertEqual(second["verification_status"], "verified")
            self.assertEqual(make_env.call_count, 2)
            self.assertEqual(make_tools.call_count, 1)
            _cleanup_disposable_environments(second)
            self.assertEqual(second["verification_environment_cache"], {})
            self.assertTrue(all(not Path(path).exists() for path in env_dirs))

    def test_setup_py_variable_requirements_are_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            (repo / "setup.py").write_text(
                "from setuptools import setup\ninstall_requires = ['requests>=2.2']\nsetup(install_requires=install_requires)\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "setup.py"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
            (repo / "marker.txt").write_text("next\n", encoding="utf-8")
            subprocess.run(["git", "add", "marker.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "migration"], cwd=repo, check=True)
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True,
            ).stdout.strip()
            self.assertIn("requests>=2.2", _requirements_at_pre_migration_commit(repo, commit))

    def test_requirement_recovery_scopes_to_the_migrated_monorepo_component(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            (repo / "setup.py").write_text(
                "from setuptools import setup\nsetup(install_requires=['root-dep==1'])\n",
                encoding="utf-8",
            )
            component = repo / "tools" / "mailer"
            component.mkdir(parents=True)
            (component / "setup.py").write_text(
                "from setuptools import setup\nsetup(install_requires=['mailer-dep==2'])\n",
                encoding="utf-8",
            )
            (component / "client.py").write_text("import legacy\n", encoding="utf-8")
            sibling = repo / "tools" / "unrelated"
            sibling.mkdir(parents=True)
            (sibling / "setup.py").write_text(
                "from setuptools import setup\nsetup(install_requires=['native-build-dep==3'])\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
            (repo / "marker.txt").write_text("next\n", encoding="utf-8")
            subprocess.run(["git", "add", "marker.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "migration"], cwd=repo, check=True)
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True,
            ).stdout.strip()
            requirements = _requirements_at_pre_migration_commit(repo, commit, "tools/mailer/client.py")
            self.assertIn("root-dep==1", requirements)
            self.assertIn("mailer-dep==2", requirements)
            self.assertNotIn("native-build-dep==3", requirements)

    def test_requirement_recovery_merges_pins_and_blocks_unsafe_includes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "client"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            (repo / "pyproject.toml").write_text(
                "[project]\ndependencies = ['requests>=2']\n", encoding="utf-8",
            )
            (repo / "requirements.txt").write_text(
                "requests==2.31.0\n-r requirements/base.txt\n", encoding="utf-8",
            )
            (repo / "requirements").mkdir()
            (repo / "requirements" / "base.txt").write_text("shared-runtime==1\n", encoding="utf-8")
            component = repo / "tools" / "mailer"
            component.mkdir(parents=True)
            (component / "setup.py").write_text(
                "from setuptools import setup\nsetup(install_requires=['mailer>=1'])\n", encoding="utf-8",
            )
            (component / "requirements.txt").write_text(
                "mailer==2\n-r ../../unrelated/requirements.txt\n", encoding="utf-8",
            )
            (component / "client.py").write_text("import legacy\n", encoding="utf-8")
            unrelated = repo / "unrelated"
            unrelated.mkdir()
            (unrelated / "requirements.txt").write_text("unrelated==9\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
            (repo / "marker.txt").write_text("next\n", encoding="utf-8")
            subprocess.run(["git", "add", "marker.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "migration"], cwd=repo, check=True)
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True,
            ).stdout.strip()
            requirements, errors = _requirements_at_pre_migration_commit(
                repo, commit, "tools/mailer/client.py", include_diagnostics=True,
            )
            self.assertIn("requests==2.31.0", requirements)
            self.assertIn("shared-runtime==1", requirements)
            self.assertIn("mailer==2", requirements)
            self.assertNotIn("requests>=2", requirements)
            self.assertNotIn("mailer>=1", requirements)
            self.assertNotIn("unrelated==9", requirements)
            self.assertTrue(any("Out-of-scope" in error for error in errors))

    def test_poetry_ranges_are_normalized_for_pip_constraints(self) -> None:
        self.assertEqual(_normalize_legacy_requirement("boto3^1.12.31"), "boto3>=1.12.31,<2.0.0")
        self.assertEqual(_normalize_legacy_requirement("azure-mgmt-resource~6"), "azure-mgmt-resource>=6,<7")
        self.assertEqual(_normalize_legacy_requirement("requests>=2.0"), "requests>=2.0")

    def test_post_rename_locustio_requirement_uses_the_real_distribution(self) -> None:
        """`locustio>=1.0` is a known impossible old name, not a package conflict."""
        self.assertEqual(
            _normalize_historical_distribution_requirement("locustio>=1.0"),
            "locust>=1.0",
        )
        self.assertEqual(
            _normalize_historical_distribution_requirement("locustio[fast]>=2.0; python_version >= '3.10'"),
            "locust[fast]>=2.0; python_version >= '3.10'",
        )
        # The old 0.x distribution remains valid and must not be silently
        # upgraded merely because AXIOM knows about the later rename.
        self.assertEqual(
            _normalize_historical_distribution_requirement("locustio==0.999"),
            "locustio==0.999",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            specs, constraints = _base_specs_and_constraints(
                ["locustio>=1.0", "requests==2.31.0"], [], temp_dir,
            )
            self.assertIn("locust>=1.0", specs)
            self.assertIsNotNone(constraints)
            self.assertIn("locust>=1.0", Path(str(constraints)).read_text(encoding="utf-8"))
        self.assertEqual(_pinned_spec("locust", ["locustio>=1.0"]), "locust>=1.0")

    def test_kafka_runtime_is_explicitly_unsupported_without_an_integration_fixture(self) -> None:
        """Network/broker APIs must record an honest reason, never a fake timing."""
        group = EquivalenceGroup(
            role="Kafka producer", candidates=["kafka-python", "confluent-kafka"],
            used_apis=["KafkaProducer"], reasoning="test",
        )
        benchmarks = {
            package: BenchmarkResult(
                package=package, import_time_ms=1.0, memory_kb=1.0,
                available=True, profile_status="measured",
            )
            for package in group.candidates
        }

        _attach_runtime_benchmarks(benchmarks, [group], python=sys.executable)

        for package in group.candidates:
            record = benchmarks[package].runtime_probes["unsupported:kafkaproducer"]
            self.assertEqual(record["status"], "unsupported")
            self.assertIn("integration fixture", record["reason"])
            self.assertEqual(benchmarks[package].runtime_ms, 0.0)
            self.assertEqual(benchmarks[package].api_runtimes, {})

    def test_json_runtime_probe_records_a_real_fixed_fixture_measurement(self) -> None:
        """The stdlib JSON probe is an actual subprocess measurement, not model output."""
        spec = next(probe for probe in RUNTIME_PROBES if probe.probe_id == "json_serialization")

        result = _run_runtime_probe("json", spec, python=sys.executable, module="json")

        self.assertEqual(result["status"], "measured")
        self.assertEqual(result["kind"], "pure_local")
        self.assertEqual(result["distribution"], "json")
        self.assertEqual(result["module"], "json")
        self.assertEqual(result["runs"], RUNTIME_SAMPLES)
        self.assertGreater(result["median_ms"], 0.0)
        self.assertGreater(result["p95_ms"], 0.0)
        self.assertTrue(result["fixture_sha256"])

    def test_runtime_registry_includes_real_local_cli_and_record_fixtures(self) -> None:
        """Common benchmark migrations have fixed semantic probes, not placeholders."""
        probes = {probe.probe_id: probe for probe in RUNTIME_PROBES}
        self.assertEqual(probes["cli_option_parse"].kind, "pure_local")
        self.assertEqual(set(probes["cli_option_parse"].candidates), {"argparse", "click", "docopt"})
        self.assertEqual(probes["record_construction"].kind, "pure_local")
        self.assertIn("attrs", probes["record_construction"].candidates)
        self.assertIn("dataclasses", probes["record_construction"].candidates)
        self.assertEqual(probes["noqa_detection"].kind, "pure_local")
        self.assertEqual(set(probes["noqa_detection"].candidates), {"pep8", "pycodestyle"})

    def test_security_network_failure_is_not_rendered_as_a_clean_low_risk_result(self) -> None:
        with patch(
            "smart_loader.agents.security_agent._fetch_url",
            return_value=_FetchResult(None, "network error: DNS unavailable"),
        ):
            result = _compute_security_result("security-regression-package")
        self.assertEqual(result.data_status, "unavailable")
        self.assertEqual(result.risk_level, "UNKNOWN")
        self.assertEqual(result.confidence, "UNAVAILABLE")
        self.assertEqual(result.days_since_update, -1)
        self.assertFalse(any("Not updated" in note for note in result.notes))

    def test_security_uses_historical_pin_and_parses_cvss_vector(self) -> None:
        package = "axiom-security-pin-test"
        pypi_payload = {
            "info": {"version": "3.0.0"},
            "releases": {
                "1.0.0": [{"upload_time_iso_8601": "2020-01-01T00:00:00Z", "yanked": False}],
                "3.0.0": [{"upload_time_iso_8601": "2025-01-01T00:00:00Z", "yanked": False}],
            },
        }
        osv_payload = {
            "results": [{"vulns": [{
                "id": "TEST-CVE",
                "summary": "test advisory",
                "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}],
            }]}],
        }

        def fetch(url: str, payload: dict | None = None, timeout: int = 8) -> _FetchResult:
            return _FetchResult(osv_payload if payload is not None else pypi_payload)

        with patch("smart_loader.agents.security_agent._fetch_url", side_effect=fetch):
            result = _compute_security_result(package, requested_version="1.0.0")
        self.assertEqual(result.data_status, "available")
        self.assertEqual(result.version, "1.0.0")
        self.assertEqual(result.latest_version, "3.0.0")
        self.assertEqual(result.version_origin, "pinned")
        self.assertEqual(result.vulnerabilities[0].severity, "CRITICAL")
        self.assertGreaterEqual(result.vulnerabilities[0].cvss_score, 9.0)
        severity, score = _advisory_severity({"severity": [{"score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}]})
        self.assertEqual(severity, "CRITICAL")
        self.assertGreaterEqual(score, 9.0)

    def test_candidate_security_reuses_pinned_source_result_under_distribution_key(self) -> None:
        source = SecurityResult(
            package="kafka", queried_package="kafka-python", version="2.0.2",
            data_status="available", overall_score=0.5,
        )
        group = EquivalenceGroup(
            role="kafka migration", candidates=["kafka-python", "confluent-kafka"],
            used_apis=[], reasoning="test",
        )
        group.__dict__["original"] = "kafka-python"
        group.__dict__["original_import"] = "kafka"
        candidate = SecurityResult(package="confluent-kafka", version="2.0.0")
        state = {
            "imports": [ImportInfo(module="kafka", alias=None, names=[], line=1)],
            "equivalence_groups": [group], "security_results": {"kafka": source}, "agent_trace": [],
        }
        with patch(
            "smart_loader.agents.security_agent._scan_packages",
            return_value={"confluent-kafka": candidate},
        ) as scan:
            out = candidate_security_agent(state)
        self.assertIs(out["security_results"]["kafka-python"], source)
        self.assertEqual(scan.call_args.args[0], ["confluent-kafka"])
        self.assertEqual(out["migration_target_versions"]["confluent-kafka"], "2.0.0")
        self.assertEqual(
            out["environment_plan"]["target_releases"]["confluent-kafka"]["install_spec"],
            "confluent-kafka==2.0.0",
        )

    def test_candidate_security_rescans_existing_target_at_current_compatible_release(self) -> None:
        """A target imported by the old project cannot retain its old pin."""
        historical_target = SecurityResult(
            package="target-package", version="1.0.0", data_status="available",
        )
        current_target = SecurityResult(
            package="target-package", version="2.4.1", data_status="available",
        )
        group = EquivalenceGroup(
            role="legacy migration", candidates=["legacy", "target-package"],
            used_apis=[], reasoning="test",
        )
        group.__dict__["original"] = "legacy"
        state = {
            "imports": [], "equivalence_groups": [group],
            "security_results": {
                "legacy": SecurityResult(package="legacy", version="1.0.0", data_status="available"),
                "target-package": historical_target,
            }, "agent_trace": [],
            "migration_target_versions": {"target_package": "2.4.1"},
            "environment_plan": {
                "interpreter_status": "ready", "selected_python_version": "3.8",
            },
        }
        with patch(
            "smart_loader.agents.security_agent._scan_packages",
            return_value={"target-package": current_target},
        ) as scan:
            out = candidate_security_agent(state)
        self.assertEqual(scan.call_args.args[0], ["target-package"])
        self.assertEqual(scan.call_args.kwargs["requested_versions"], {"target-package": "2.4.1"})
        self.assertIs(out["security_results"]["target-package"], current_target)
        self.assertEqual(out["migration_target_versions"]["target-package"], "2.4.1")

    def test_candidate_security_fails_closed_when_historical_python_is_unavailable(self) -> None:
        group = EquivalenceGroup(
            role="legacy migration", candidates=["legacy", "target"],
            used_apis=[], reasoning="test",
        )
        group.__dict__["original"] = "legacy"
        state = {
            "imports": [], "equivalence_groups": [group], "security_results": {}, "agent_trace": [],
            "environment_plan": {"interpreter_status": "interpreter_unavailable"},
        }
        with patch("smart_loader.agents.security_agent._scan_packages") as scan:
            out = candidate_security_agent(state)
        scan.assert_not_called()
        self.assertIn("target", out["target_resolution_failures"])
        self.assertEqual(
            out["environment_plan"]["target_releases"]["target"]["selection"],
            "blocked_environment_plan",
        )

    def test_source_security_scan_uses_historical_python_only_for_stdlib_detection(self) -> None:
        seen: list[tuple] = []

        def scan(*args):
            seen.append(args)
            return SecurityResult(package="dataclasses", version="0.8", data_status="available")

        with patch("smart_loader.agents.security_agent._compute_security_result", side_effect=scan):
            security_agent({
                "imports": [ImportInfo(module="dataclasses", alias=None, names=[], line=1)],
                "base_requirements": ["dataclasses==0.8"],
                "environment_plan": {"selected_python_version": "3.6"},
                "agent_trace": [],
            })
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][3], "3.6")
        self.assertFalse(seen[0][4])

    def test_unavailable_security_data_uses_neutral_axiom_score(self) -> None:
        unavailable = _compute_security_result("argparse")  # stdlib -> N/A, also neutral
        available_like = type("Security", (), {"data_status": "available", "overall_score": 0.1})()
        group = EquivalenceGroup(
            role="test", candidates=["legacy", "target"], used_apis=[], reasoning="test",
        )
        benchmarks = {
            "legacy": BenchmarkResult(package="legacy", import_time_ms=1.0, memory_kb=1.0, available=True),
            "target": BenchmarkResult(package="target", import_time_ms=1.0, memory_kb=1.0, available=True),
        }
        with patch(
            "smart_loader.agents.axiom_agent._score_api_compat",
            return_value=({"legacy": 0.5, "target": 0.5}, {"legacy": 1.0, "target": 1.0}),
        ):
            _, _, scores, _, _ = _compute_scores(
                group, benchmarks, {"legacy": unavailable, "target": available_like}, "test", "test",
            )
        self.assertIsNone(scores["legacy"]["security"])
        self.assertFalse(scores["legacy"]["security_evidence"])
        self.assertIsNone(scores["target"]["security"])
        self.assertFalse(scores["target"]["security_evidence"])

    def test_hard_isolated_profile_failure_cannot_win_axiom_scoring(self) -> None:
        group = EquivalenceGroup(
            role="legacy -> broken", candidates=["legacy", "broken"],
            used_apis=["client.call"], reasoning="test",
        )
        benchmarks = {
            "legacy": BenchmarkResult(
                package="legacy", import_time_ms=10.0, memory_kb=10.0,
                available=True, profile_status="measured",
            ),
            "broken": BenchmarkResult(
                package="broken", import_time_ms=0.0, memory_kb=0.0,
                available=False, error="pip install failed", profile_status="hard_install_failure",
            ),
        }
        with patch(
            "smart_loader.agents.axiom_agent._score_api_compat",
            return_value=({"legacy": 0.1, "broken": 1.0}, {"legacy": 1.0, "broken": 20.0}),
        ):
            winner, _confidence, scores, _crypto, _uninstalled = _compute_scores(
                group, benchmarks, {}, "test", "test",
            )
        self.assertEqual(winner, "legacy")
        self.assertNotIn("broken", scores)

    def test_neutralized_profile_result_remains_eligible_but_is_not_measured(self) -> None:
        group = EquivalenceGroup(
            role="legacy -> target", candidates=["legacy", "target"],
            used_apis=["client.call"], reasoning="test",
        )
        benchmarks = {
            "legacy": BenchmarkResult(
                package="legacy", import_time_ms=10.0, memory_kb=10.0,
                available=True, profile_status="measured",
            ),
            "target": BenchmarkResult(
                package="target", import_time_ms=0.0, memory_kb=0.0,
                available=False, profile_status="neutral_shared_import_root",
            ),
        }
        with patch(
            "smart_loader.agents.axiom_agent._score_api_compat",
            return_value=({"legacy": 0.1, "target": 1.0}, {"legacy": 1.0, "target": 20.0}),
        ):
            winner, confidence, scores, _crypto, uninstalled = _compute_scores(
                group, benchmarks, {}, "test", "test",
            )
        self.assertEqual(winner, "target")
        self.assertIn("target", scores)
        self.assertIn("target", uninstalled)
        self.assertNotEqual(confidence, "HIGH")

    def test_missing_api_evidence_routes_to_connector_instead_of_crashing_or_drop_in(self) -> None:
        """No-fallback scoring uses None, which must be treated as unsafe routing."""
        decision = self._decision()
        decision.score = {"api_match": None}
        group = EquivalenceGroup(
            role=decision.role, candidates=["legacy", "target"], used_apis=["call"], reasoning="test",
        )
        self.assertEqual(
            _after_axiom({"decisions": [decision], "equivalence_groups": [group]}),
            "connector",
        )
        self.assertEqual(
            _after_axiom_without_connector({"decisions": [decision], "equivalence_groups": [group]}),
            "verifier",
        )

    def test_axiom_never_uses_llm_runtime_estimate_when_no_real_probe_exists(self) -> None:
        """An arbitrarily fast model estimate must not decide a no-probe migration."""
        group = EquivalenceGroup(
            role="Kafka producer", candidates=["legacy", "target"],
            used_apis=["KafkaProducer"], reasoning="test",
        )
        benchmarks = {
            package: BenchmarkResult(
                package=package, import_time_ms=1.0, memory_kb=1.0,
                available=True, profile_status="measured",
                runtime_probes={
                    "unsupported:kafkaproducer": {
                        "status": "unsupported", "reason": "integration fixture required",
                    },
                },
            )
            for package in group.candidates
        }
        # The compatibility opinion is deliberately tied.  If AXIOM used the
        # old ``runtime_speed_factor`` fallback, target would win solely due
        # to this invented 100x runtime advantage.  With no real shared probe
        # the original must remain tied/win by stable ordering instead.
        with patch(
            "smart_loader.agents.axiom_agent._score_api_compat",
            return_value=({"legacy": 0.5, "target": 0.5}, {"legacy": 1.0, "target": 100.0}),
        ):
            winner, _confidence, scores, _crypto, _unmeasured = _compute_scores(
                group, benchmarks, {}, "test", "test",
            )

        self.assertEqual(winner, "legacy")
        self.assertEqual(scores["legacy"]["speed"], scores["target"]["speed"])
        self.assertFalse(scores["legacy"]["runtime_evidence"])
        self.assertFalse(scores["target"]["runtime_evidence"])

    def test_historical_snapshot_is_outside_the_live_client_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "client"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            (repo / "client.py").write_text("value = 'before'\n", encoding="utf-8")
            subprocess.run(["git", "add", "client.py"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
            (repo / "client.py").write_text("value = 'after'\n", encoding="utf-8")
            subprocess.run(["git", "commit", "-am", "migration", "-q"], cwd=repo, check=True)
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True,
            ).stdout.strip()
            snapshot = _historical_project_snapshot(repo, commit)
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertNotIn(str(repo), str(snapshot))
            self.assertEqual((snapshot / "client.py").read_text(encoding="utf-8"), "value = 'before'\n")

    def test_metrics_do_not_count_rejected_target_as_verified(self) -> None:
        case = EvalCase(
            migration_id="metric-test", repo="example/repo", domain="test",
            source="legacy", target="target", file_path="client.py", before_code="import legacy\n",
        )
        rejected = EvalResult(
            case=case, recommended_target=True, scoreable_full_pipeline=True,
            outcome="verification_rejected", verification_status="rejected",
        )
        verified = EvalResult(
            case=case, recommended_target=True, scoreable_full_pipeline=True,
            outcome="ground_truth_verified", verification_status="verified",
            verified_ground_truth=True, patch_changed=True,
        )
        metrics = compute_metrics([rejected, verified])
        self.assertEqual(metrics["target_selection_rate_pct"], 100.0)
        self.assertEqual(metrics["verified_ground_truth_n"], 1)
        self.assertEqual(metrics["verified_ground_truth_rate_pct"], 50.0)

    def test_pymigbench_environment_plan_uses_recovered_requirements_and_auto_target(self) -> None:
        """Planning is local/read-only: historical base specs, never invented target pins."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            version = f"{sys.version_info.major}.{sys.version_info.minor}"
            (root / "pyproject.toml").write_text(
                f"[project]\nrequires-python = '>={version}'\n", encoding="utf-8",
            )
            case = EvalCase(
                migration_id="environment-plan", repo="example/repo", domain="test",
                source="legacy-client", target="secure-target", file_path="client.py",
                before_code="import legacy_client\n", project_root=str(root),
                base_requirements=["legacy-client==1.2", "shared-runtime==4.0"],
            )
            with patch(
                "smart_loader.core.environment_plan.available_interpreters",
                return_value={version: sys.executable},
            ):
                plan = _build_case_environment_plan(case)

        self.assertEqual(plan["requirements"], case.base_requirements)
        self.assertEqual(plan["target_distribution"], "secure-target")
        self.assertEqual(plan["target_version"], "auto")
        self.assertEqual(plan["target_requirement"], "secure-target")
        self.assertEqual(plan["selected_python"], sys.executable)
        self.assertEqual(plan["interpreter_status"], "ready")

        inputs = _environment_pipeline_inputs(plan, case.target)
        self.assertEqual(inputs["environment_python"], sys.executable)
        self.assertEqual(inputs["migration_target"], "secure-target")
        self.assertEqual(inputs["migration_target_version"], "auto")
        self.assertEqual(inputs["migration_target_versions"], {"secure-target": "auto"})

    def test_full_pipeline_forwards_environment_plan_to_graph_runner(self) -> None:
        """The standard full path receives the same plan as the seeded evaluator path."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            version = f"{sys.version_info.major}.{sys.version_info.minor}"
            (root / "pyproject.toml").write_text(
                f"[project]\nrequires-python = '>={version}'\n", encoding="utf-8",
            )
            case = EvalCase(
                migration_id="environment-forwarding", repo="example/repo", domain="test",
                source="legacy", target="target", file_path="client.py",
                before_code="import legacy\n", project_root=str(root),
                base_requirements=["legacy==1.0"],
            )
            captured: dict = {}

            def fake_run_loader(_path: str, **kwargs: object) -> dict:
                captured.update(kwargs)
                return {
                    "environment_plan": kwargs["environment_plan"],
                    "decisions": [], "attempted_decision_history": [],
                    "patched_code": case.before_code, "benchmarks": {},
                    "verification_status": "not_run", "verification_environment_status": "unknown",
                    "agent_trace": [], "connectors": {},
                }

            with patch(
                "smart_loader.core.environment_plan.available_interpreters",
                return_value={version: sys.executable},
            ), patch("smart_loader.core.graph.run_loader", side_effect=fake_run_loader):
                result = run_full_pipeline(case, "test", "test", isolated_profiling=True)

        self.assertEqual(captured["migration_target"], "target")
        self.assertEqual(captured["migration_target_version"], "auto")
        self.assertEqual(captured["environment_python"], sys.executable)
        self.assertEqual(captured["environment_plan"]["requirements"], ["legacy==1.0"])
        self.assertEqual(result.environment_plan["interpreter_status"], "ready")
        self.assertEqual(result.environment_plan["target_version"], "auto")

    def test_compact_environment_plan_keeps_status_and_diagnostics_not_raw_requirements(self) -> None:
        compact = _compact_environment_plan({
            "interpreter_status": "interpreter_unavailable",
            "environment_backend": "docker_recommended",
            "selected_python": "",
            "target_distribution": "target", "target_version": "auto",
            "target_requirement": "target", "requirements": ["private-package==1"],
            "requirement_files": ["/tmp/requirements.txt"],
            "warnings": ["Python 3.7 is unavailable"],
            "recovery_hints": ["Use a compatible container"],
        })
        self.assertEqual(compact["interpreter_status"], "interpreter_unavailable")
        self.assertEqual(compact["requirement_count"], 1)
        self.assertEqual(compact["warnings"], ["Python 3.7 is unavailable"])
        self.assertNotIn("requirements", compact)

    def test_metrics_do_not_count_blocked_environments_as_verifier_rejections(self) -> None:
        case = EvalCase(
            migration_id="metric-environment-test", repo="example/repo", domain="test",
            source="legacy", target="target", file_path="client.py", before_code="import legacy\n",
        )
        blocked = EvalResult(
            case=case, recommended_target=True, scoreable_full_pipeline=True,
            outcome="environment_incomplete", verification_status="environment_incomplete",
            verification_environment_status="blocked",
        )
        rejected = EvalResult(
            case=case, recommended_target=True, scoreable_full_pipeline=True,
            outcome="verification_rejected", verification_status="rejected",
        )
        metrics = compute_metrics([blocked, rejected])
        self.assertEqual(metrics["verification_environment_incomplete_n"], 1)
        self.assertEqual(metrics["verifier_n"], 1)
        self.assertEqual(metrics["execution_ready_n"], 1)
        self.assertEqual(metrics["verifier_clean_rate_pct"], 0.0)

    def test_unchanged_source_decision_is_not_credited_as_alternative(self) -> None:
        case = EvalCase(
            migration_id="incidental-test", repo="example/repo", domain="test",
            source="legacy", target="target", file_path="client.py", before_code="import legacy\n",
        )
        source = LoadDecision(role="legacy", winner="legacy", original="legacy", score={}, rationale="keep")
        incidental = LoadDecision(role="other", winner="new-other", original="other", score={}, rationale="change")
        state = {
            "decisions": [source, incidental], "patched_code": "import legacy\nimport new_other\n",
            "verification_status": "verified", "verification_environment_status": "ready",
        }
        result = _result_from_state(case, state, detected_group=True)
        self.assertEqual(result.selection_status, "incidental")
        self.assertEqual(result.outcome, "verified_incidental_migration")
        self.assertFalse(result.verified_alternative)

    def test_attempted_target_is_preserved_after_verifier_reverts_final_decision(self) -> None:
        case = EvalCase(
            migration_id="attempt-history", repo="example/repo", domain="test",
            source="legacy", target="target", file_path="client.py", before_code="import legacy\n",
        )
        reverted = LoadDecision(role="legacy", winner="legacy", original="legacy", score={}, rationale="reverted")
        state = {
            "decisions": [reverted],
            "attempted_decision_history": [[{
                "role": "legacy", "original": "legacy", "winner": "target", "confidence": "HIGH",
            }]],
            "patched_code": "import legacy\n",
            "verification_status": "rejected", "verification_environment_status": "ready",
        }
        result = _result_from_state(case, state, detected_group=True)
        self.assertTrue(result.recommended_target)
        self.assertEqual(result.attempted_axiom_winner, "target")
        self.assertEqual(result.axiom_winner, None)
        self.assertEqual(result.selection_status, "no_migration")
        self.assertEqual(result.outcome, "verification_rejected")

    def test_explicit_target_exclusion_is_reported_as_infeasible(self) -> None:
        case = EvalCase(
            migration_id="target-infeasible", repo="example/repo", domain="test",
            source="legacy", target="target", file_path="client.py", before_code="import legacy\n",
        )
        state = {
            "decisions": [LoadDecision(
                role="legacy client", winner="legacy", original="legacy", score={}, rationale="keep",
            )],
            "verification_status": "not_applicable",
            "verification_failures": {"legacy client": ["target"]},
            "_last_verifier_feedback": {
                "errors": [{"message": "target cannot implement the required synchronous API"}],
            },
        }
        result = _result_from_state(case, state, detected_group=True, seeded=True)
        self.assertEqual(result.outcome, "target_infeasible")
        self.assertEqual(result.target_infeasibility_evidence[0]["candidate"], "target")
        self.assertIn("synchronous API", result.target_infeasibility_evidence[0]["verifier_evidence"][0])
        self.assertEqual(compute_metrics([result])["proven_target_infeasible_n"], 1)

    def test_incomplete_environment_is_not_reported_as_target_infeasible(self) -> None:
        case = EvalCase(
            migration_id="environment-not-infeasible", repo="example/repo", domain="test",
            source="legacy", target="target", file_path="client.py", before_code="import legacy\n",
        )
        state = {
            "decisions": [LoadDecision(
                role="legacy client", winner="legacy", original="legacy", score={}, rationale="keep",
            )],
            "verification_status": "environment_incomplete",
            "verification_failures": {"legacy client": ["target"]},
        }
        result = _result_from_state(case, state, detected_group=True, seeded=True)
        self.assertEqual(result.outcome, "environment_incomplete")


if __name__ == "__main__":
    unittest.main()
