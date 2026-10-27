"""Regression tests for evidence-backed historical environment planning."""

from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

from smart_loader.agents.security_agent import _PYPI_CACHE, _is_stdlib_for_python, _select_compatible_release
from smart_loader.core.environment_plan import _normalise_python_constraint, plan_project_environment


class EnvironmentPlanTests(unittest.TestCase):
    def test_prefers_a_local_interpreter_compatible_with_project_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.joinpath("pyproject.toml").write_text(
                "[project]\nrequires-python = '>=3.7,<3.9'\n", encoding="utf-8",
            )
            with patch(
                "smart_loader.core.environment_plan.available_interpreters",
                return_value={"3.8": "/opt/python3.8", "3.12": "/opt/python3.12"},
            ):
                plan = plan_project_environment(root)
        self.assertTrue(plan.usable)
        self.assertEqual(plan.selected_python, "/opt/python3.8")
        self.assertEqual(plan.selected_python_version, "3.8")
        self.assertEqual(plan.python_constraint, ">=3.7,<3.9")

    def test_unavailable_historical_interpreter_does_not_fall_back_to_host(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.joinpath(".python-version").write_text("3.8.18\n", encoding="utf-8")
            with patch(
                "smart_loader.core.environment_plan.available_interpreters",
                return_value={"3.12": "/opt/python3.12"},
            ):
                plan = plan_project_environment(root)
        self.assertFalse(plan.usable)
        self.assertEqual(plan.interpreter_status, "interpreter_unavailable")
        self.assertFalse(plan.selected_python)
        self.assertTrue(any("no matching local interpreter" in warning for warning in plan.warnings))

    def test_explicit_incompatible_interpreter_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.joinpath("setup.cfg").write_text(
                "[options]\npython_requires = >=3.8,<3.10\n", encoding="utf-8",
            )
            with patch(
                "smart_loader.core.environment_plan.available_interpreters",
                return_value={"3.12": "/opt/python3.12"},
            ), patch(
                "smart_loader.core.environment_plan._resolve_requested_interpreter",
                return_value=("/opt/python3.12", "3.12"),
            ):
                plan = plan_project_environment(root, requested_python="python3.12")
        self.assertEqual(plan.interpreter_status, "interpreter_incompatible")
        self.assertFalse(plan.usable)

    def test_unresolved_explicit_interpreter_never_falls_back_to_host(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch(
                "smart_loader.core.environment_plan._resolve_requested_interpreter",
                return_value=("", ""),
            ):
                plan = plan_project_environment(root, requested_python="python3.8-missing")
        self.assertEqual(plan.interpreter_status, "interpreter_unavailable")
        self.assertFalse(plan.selected_python)
        self.assertTrue(any("could not be resolved" in warning for warning in plan.warnings))

    def test_selects_newest_stable_non_yanked_release_for_selected_python(self) -> None:
        payload = {
            "info": {"version": "3.0.0"},
            "releases": {
                "1.0.0": [{"yanked": False, "requires_python": ">=3.7"}],
                "2.0.0": [{"yanked": False, "requires_python": ">=3.8"}],
                "3.0.0": [{"yanked": False, "requires_python": ">=3.11"}],
                "4.0.0rc1": [{"yanked": False, "requires_python": ">=3.8"}],
                "2.1.0": [{"yanked": True, "requires_python": ">=3.8"}],
            },
        }
        with patch.dict(_PYPI_CACHE, {"example-target": payload}, clear=False):
            version, error = _select_compatible_release("example-target", "3.8")
        self.assertEqual(version, "2.0.0")
        self.assertIsNone(error)

    def test_requested_incompatible_target_is_not_silently_downgraded_or_upgraded(self) -> None:
        payload = {
            "info": {"version": "3.0.0"},
            "releases": {
                "2.0.0": [{"yanked": False, "requires_python": ">=3.8"}],
                "3.0.0": [{"yanked": False, "requires_python": ">=3.11"}],
            },
        }
        with patch.dict(_PYPI_CACHE, {"example-target": payload}, clear=False):
            version, error = _select_compatible_release("example-target", "3.8", "3.0.0")
        self.assertFalse(version)
        self.assertIn("compatible with Python 3.8", error or "")

    def test_stdlib_target_is_version_aware_for_historical_python(self) -> None:
        self.assertFalse(_is_stdlib_for_python("dataclasses", "3.6"))
        self.assertTrue(_is_stdlib_for_python("dataclasses", "3.7"))

    def test_pep440_compatible_release_operator_is_not_treated_as_poetry_tilde(self) -> None:
        # PEP 440 ``~=3.8`` allows Python 3.8 through <4.0.  Poetry's
        # ``~3.8`` is the distinct minor-only shorthand and remains
        # normalized separately.
        self.assertEqual(_normalise_python_constraint("~=3.8"), "~=3.8")
        self.assertEqual(_normalise_python_constraint("~3.8"), ">=3.8,<3.9")

    def test_planner_recovers_pep621_and_nested_runtime_requirement_pins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.joinpath("pyproject.toml").write_text(
                "[project]\n"
                "requires-python = '>=3.8'\n"
                "dependencies = ['requests>=2.28']\n",
                encoding="utf-8",
            )
            root.joinpath("requirements.txt").write_text(
                "urllib3==2.2.1\n-r requirements/pins.txt\n", encoding="utf-8",
            )
            root.joinpath("requirements").mkdir()
            root.joinpath("requirements/pins.txt").write_text(
                "idna==3.7\n", encoding="utf-8",
            )
            with patch(
                "smart_loader.core.environment_plan.available_interpreters",
                return_value={"3.12": sys.executable},
            ):
                plan = plan_project_environment(root)
        self.assertIn("urllib3==2.2.1", plan.requirements)
        self.assertIn("idna==3.7", plan.requirements)
        self.assertIn("requests>=2.28", plan.requirements)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
