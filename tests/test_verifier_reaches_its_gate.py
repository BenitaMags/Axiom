"""The Verifier must actually reach its acceptance gate.

Measured over 36 ``proteus_full`` runs: ``vca_reentries`` was 0 on 34 and
``converged=Y`` on all 36, including all 10 failures. The natural reading is
that the acceptance gate was too lenient. It was not -- the Verifier returned
``environment_incomplete`` and never evaluated the gate at all, because two
pieces of its own instrumentation were broken:

    1. ``run_axiom_arms`` never passed ``verification_file_path``, so the
       project overlay took its fallback branch and wrote the patched code
       alone into an empty temp dir as ``migration.py``. ``pytest -q`` there
       reported "no tests ran in 0.00s", which is a baseline blocker.

    2. Bandit was handed the application's site-packages. On a machine with
       the PyPI ``argparse`` backport installed, that shadows the stdlib
       ``argparse``, whose ``FileType`` gained ``encoding`` long ago; bandit's
       CLI parser then dies at import. A tool_error is also a baseline blocker.

Either one alone is enough to make the whole Verification agent inert while
every table still renders as though it had run -- the worst failure mode,
because the check looks present.
"""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CORPUS = os.path.expanduser("~/Downloads/evaluation")
PROGRAM = os.path.join(CORPUS, "D3_telemetry_gateway")


# ── Blocker 1: the overlay must be the project, not one orphaned file ────────

@pytest.mark.skipif(not os.path.isdir(PROGRAM), reason="corpus not present")
def test_overlay_without_a_relative_path_loses_the_project():
    """Pins the broken behaviour so the cause stays legible."""
    from smart_loader.agents.verifier_agent import _project_overlay
    code = open(os.path.join(PROGRAM, "sink.py"), encoding="utf-8").read()
    with _project_overlay(PROGRAM, "", code) as (root, _):
        assert sorted(os.listdir(root)) == ["migration.py"]


@pytest.mark.skipif(not os.path.isdir(PROGRAM), reason="corpus not present")
def test_overlay_with_a_relative_path_keeps_the_project_and_its_tests():
    from smart_loader.agents.verifier_agent import _project_overlay
    code = open(os.path.join(PROGRAM, "sink.py"), encoding="utf-8").read()
    with _project_overlay(PROGRAM, "sink.py", code) as (root, target):
        present = set(os.listdir(root))
        assert {"sink.py", "gateway.py", "estimator.py"} <= present
        assert any(n.startswith("test_") for n in present), \
            "without the suite the baseline reports 'no tests ran', a blocker"
        assert os.path.basename(target) == "sink.py"
        result = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=root,
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stdout[-500:]


def test_run_axiom_arms_passes_the_relative_path():
    """The call site, not just the helper. This is the line that was missing."""
    source = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "run_axiom_arms.py"), encoding="utf-8").read()
    assert "verification_file_path=name," in source, \
        "the overlay silently degrades to a single orphaned file without this"


# ── Blocker 2: bandit must not be shadowed by the application's packages ─────

def test_bandit_does_not_receive_application_site_packages():
    """Bandit reads source text; it never imports the app, so it must not see it.

    Handing it the app environment is what let a stray ``argparse`` shim in
    site-packages take the whole tool down.
    """
    source = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "smart_loader", "agents", "verifier_agent.py"),
                  encoding="utf-8").read()
    assert "bandit_env = _verification_environment(tool_paths)" in source
    call = source[source.index('"-m", "bandit"'):]
    call = call[:call.index(")") + 1]
    assert "env=bandit_env" in call, "bandit must not be given the app env"



@pytest.mark.skipif(not os.path.isdir(PROGRAM), reason="corpus not present")
def test_bandit_actually_runs_in_a_provisioned_environment():
    """End to end, because the failure only appeared through this path."""
    from smart_loader.agents.profiler_agent import create_static_analysis_environment
    from smart_loader.agents.verifier_agent import _run_static_analysis
    _, python, status = create_static_analysis_environment()
    if status.get("bandit") != "available":
        pytest.skip(f"bandit could not be provisioned: {status.get('bandit')}")
    report = _run_static_analysis(
        python_executable=python, runtime_python_executable=sys.executable,
        source_path=os.path.join(PROGRAM, "sink.py"),
        cwd=PROGRAM, import_paths=[],
    )
    bandit = report.get("bandit", {})
    assert bandit.get("status") != "tool_error", bandit.get("raw", "")[:400]
