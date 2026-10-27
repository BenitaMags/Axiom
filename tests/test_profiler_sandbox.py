"""Focused regression tests for isolated profiler environment hardening."""

from __future__ import annotations

import tempfile
import unittest
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from smart_loader.agents.profiler_agent import (
    _base_specs_and_constraints,
    create_verification_environment,
    create_static_analysis_environment,
    _neutralize_shared_import_root_collisions,
    _pip_install,
    _verification_subprocess_environment,
    profiler_agent,
)
from smart_loader.core.state import BenchmarkResult, EquivalenceGroup


class ProfilerSandboxTests(unittest.TestCase):
    def test_explicit_source_and_target_are_not_base_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_specs, constraints = _base_specs_and_constraints(
                [
                    "kafka-python==2.0.2",
                    "confluent-kafka==2.5.0",
                    "requests==2.31.0",
                    "locustio==1.6.0",
                ],
                ["kafka", "confluent-kafka", "locust"],
                temp_dir,
            )
            self.assertEqual(base_specs, ["requests==2.31.0"])
            self.assertIsNotNone(constraints)
            self.assertEqual(Path(constraints).read_text(encoding="utf-8"), "requests==2.31.0\n")

    def test_candidate_install_uses_upgrade_and_constraints(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        with patch("smart_loader.agents.profiler_agent.subprocess.run", return_value=completed) as run:
            ok, detail = _pip_install(
                "sandbox-python", "confluent-kafka",
                constraints_path="/tmp/base-constraints.txt", upgrade=True,
            )
        self.assertTrue(ok)
        self.assertEqual(detail, "")
        command = run.call_args.args[0]
        self.assertIn("--upgrade", command)
        self.assertEqual(command[command.index("--constraint") + 1], "/tmp/base-constraints.txt")
        self.assertEqual(command[-1], "confluent-kafka")

    def test_installer_environment_does_not_inherit_model_credentials(self) -> None:
        with patch.dict("os.environ", {"NVIDIA_API_KEY": "secret", "PATH": "/bin"}, clear=True):
            env = _verification_subprocess_environment()
        self.assertNotIn("NVIDIA_API_KEY", env)
        self.assertEqual(env["PATH"], "/bin")

    def test_verifier_target_env_excludes_old_source_and_target_pins(self) -> None:
        with patch("smart_loader.agents.profiler_agent._create_disposable_venv", return_value="/tmp/axiom-test-python"), \
             patch("smart_loader.agents.profiler_agent._pip_install_many", return_value=(True, "")) as install_many, \
             patch("smart_loader.agents.profiler_agent._pip_install", return_value=(True, "")) as install, \
             patch("smart_loader.agents.profiler_agent._pip_check", return_value=(True, "")):
            env_dir, _python, failures, _tools = create_verification_environment(
                ["kafka-python==2.0.2", "confluent-kafka==1.9.0", "requests==2.31.0"],
                "kafka", ["confluent-kafka"], include_source=False,
            )
        try:
            self.assertEqual(failures, {})
            calls = [(call.args[1], call.kwargs) for call in install.call_args_list]
            target_call = next(kwargs for spec, kwargs in calls if spec == "confluent-kafka")
            self.assertTrue(target_call["upgrade"])
            batch_specs = install_many.call_args.args[1]
            constraint_path = install_many.call_args.kwargs["constraints_path"]
            self.assertEqual(batch_specs, ["requests==2.31.0"])
            self.assertIsNotNone(constraint_path)
            self.assertEqual(Path(constraint_path).read_text(encoding="utf-8"), "requests==2.31.0\n")
            self.assertNotIn("kafka-python==2.0.2", [spec for spec, _ in calls])
            self.assertNotIn("confluent-kafka==1.9.0", [spec for spec, _ in calls])
        finally:
            shutil.rmtree(env_dir, ignore_errors=True)

    def test_static_toolchain_is_separate_from_historical_application_environment(self) -> None:
        with patch("smart_loader.agents.profiler_agent._create_disposable_venv", return_value="/tmp/axiom-test-python"), \
             patch("smart_loader.agents.profiler_agent._pip_install_many", return_value=(True, "")), \
             patch("smart_loader.agents.profiler_agent._pip_install", return_value=(True, "")) as install, \
             patch("smart_loader.agents.profiler_agent._pip_check", return_value=(True, "")):
            env_dir, _python, failures, tools = create_verification_environment(
                ["pylint==1.5.4", "requests==2.31.0"], "legacy", ["target"], include_source=False,
            )
        try:
            self.assertEqual(failures, {})
            self.assertEqual(tools, {})
            calls = [(call.args[1], call.kwargs) for call in install.call_args_list]
            self.assertFalse(any(spec.startswith(("pylint>=", "bandit>=", "mypy>=")) for spec, _ in calls))
        finally:
            shutil.rmtree(env_dir, ignore_errors=True)

        with patch("smart_loader.agents.profiler_agent._create_disposable_venv", return_value="/tmp/axiom-test-python"), \
             patch("smart_loader.agents.profiler_agent._pip_install", return_value=(True, "")) as install, \
             patch("smart_loader.agents.profiler_agent._pip_check", return_value=(True, "")):
            tools_dir, _python, tool_status = create_static_analysis_environment()
        try:
            self.assertEqual(set(tool_status.values()), {"available"})
            for call in install.call_args_list:
                spec, kwargs = call.args[1], call.kwargs
                self.assertTrue(spec.startswith(("pylint>=", "bandit>=", "mypy>=")))
                self.assertIsNone(kwargs.get("constraints_path"))
                self.assertTrue(kwargs.get("upgrade"))
        finally:
            shutil.rmtree(tools_dir, ignore_errors=True)

    def test_verifier_baseline_uses_python_stdlib_without_pip_backport(self) -> None:
        with patch("smart_loader.agents.profiler_agent._create_disposable_venv", return_value="/tmp/axiom-test-python"), \
             patch("smart_loader.agents.profiler_agent._pip_install_many", return_value=(True, "")) as install_many, \
             patch("smart_loader.agents.profiler_agent._pip_install", return_value=(True, "")) as install, \
             patch("smart_loader.agents.profiler_agent._pip_check", return_value=(True, "")):
            env_dir, _python, failures, _tools = create_verification_environment(
                ["requests==2.31.0"], "argparse", [], include_source=True,
            )
        try:
            self.assertEqual(failures, {})
            installed_specs = [call.args[1] for call in install.call_args_list]
            self.assertNotIn("argparse", installed_specs)
            self.assertEqual(install_many.call_args.args[1], ["requests==2.31.0"])
        finally:
            shutil.rmtree(env_dir, ignore_errors=True)

    def test_locustio_to_locust_installs_the_current_target_not_the_source_pin(self) -> None:
        with patch("smart_loader.agents.profiler_agent._create_disposable_venv", return_value="/tmp/axiom-test-python"), \
             patch("smart_loader.agents.profiler_agent._pip_install_many", return_value=(True, "")), \
             patch("smart_loader.agents.profiler_agent._pip_install", return_value=(True, "")) as install, \
             patch("smart_loader.agents.profiler_agent._pip_check", return_value=(True, "")):
            env_dir, _python, failures, _tools = create_verification_environment(
                ["locustio==1.6.0"], "locustio", ["locust"], include_source=False,
            )
        try:
            self.assertEqual(failures, {})
            installed_specs = [call.args[1] for call in install.call_args_list]
            self.assertIn("locust", installed_specs)
            self.assertNotIn("locustio==1.6.0", installed_specs)
        finally:
            shutil.rmtree(env_dir, ignore_errors=True)

    def test_verifier_target_env_uses_security_resolved_exact_target_release(self) -> None:
        with patch("smart_loader.agents.profiler_agent._create_disposable_venv", return_value="/tmp/axiom-test-python"), \
             patch("smart_loader.agents.profiler_agent._pip_install_many", return_value=(True, "")), \
             patch("smart_loader.agents.profiler_agent._pip_install", return_value=(True, "")) as install, \
             patch("smart_loader.agents.profiler_agent._pip_check", return_value=(True, "")):
            env_dir, _python, failures, _tools = create_verification_environment(
                ["requests==2.31.0"], "legacy", ["target"], include_source=False,
                target_versions={"target": "2.4.1"},
            )
        try:
            self.assertEqual(failures, {})
            installed_specs = [call.args[1] for call in install.call_args_list]
            self.assertIn("target==2.4.1", installed_specs)
        finally:
            shutil.rmtree(env_dir, ignore_errors=True)

    def test_verifier_target_pin_accepts_equivalent_import_spelling(self) -> None:
        with patch("smart_loader.agents.profiler_agent._create_disposable_venv", return_value="/tmp/axiom-test-python"), \
             patch("smart_loader.agents.profiler_agent._pip_install_many", return_value=(True, "")), \
             patch("smart_loader.agents.profiler_agent._pip_install", return_value=(True, "")) as install, \
             patch("smart_loader.agents.profiler_agent._pip_check", return_value=(True, "")):
            env_dir, _python, failures, _tools = create_verification_environment(
                ["requests==2.31.0"], "legacy", ["target-package"], include_source=False,
                target_versions={"target_package": "2.4.1"},
            )
        try:
            self.assertEqual(failures, {})
            installed_specs = [call.args[1] for call in install.call_args_list]
            self.assertIn("target-package==2.4.1", installed_specs)
        finally:
            shutil.rmtree(env_dir, ignore_errors=True)

    def test_isolated_profiler_blocks_every_unresolved_non_source_candidate(self) -> None:
        group = EquivalenceGroup(
            role="legacy migration", candidates=["legacy", "target-alternative"],
            used_apis=[], reasoning="test",
        )
        group.__dict__["original"] = "legacy"
        measured = {
            "legacy": BenchmarkResult(package="legacy", import_time_ms=1.0, memory_kb=1.0, available=True),
            "target-alternative": BenchmarkResult(
                package="target-alternative", import_time_ms=1.0, memory_kb=1.0, available=True,
            ),
        }
        with patch(
            "smart_loader.agents.profiler_agent._profile_in_temporary_venv",
            return_value=(measured, "/tmp/profile", "/tmp/profile/bin/python", {}, {}),
        ) as profile, patch("smart_loader.agents.profiler_agent._attach_runtime_benchmarks"):
            profiler_agent({
                "equivalence_groups": [group], "isolated_profiling": True,
                "environment_plan": {"interpreter_status": "ready"},
                "base_requirements": [], "migration_target_versions": {},
                "target_resolution_failures": {}, "imports": [], "agent_trace": [],
            })
        failures = profile.call_args.kwargs["target_resolution_failures"]
        self.assertNotIn("legacy", failures)
        self.assertIn("target-alternative", failures)

    def test_shared_import_root_results_are_neutralized(self) -> None:
        benchmarks = {
            "crypto": BenchmarkResult(
                package="crypto", import_time_ms=4.0, memory_kb=8.0,
                available=True, error="import module: Crypto",
            ),
            "pycryptodome": BenchmarkResult(
                package="pycryptodome", import_time_ms=2.0, memory_kb=6.0,
                available=True, error="import module: Crypto",
            ),
        }
        _neutralize_shared_import_root_collisions(benchmarks)
        for result in benchmarks.values():
            self.assertFalse(result.available)
            self.assertEqual(result.import_time_ms, 0.0)
            self.assertIn("Shared import root 'crypto'", result.error or "")


if __name__ == "__main__":
    unittest.main()
