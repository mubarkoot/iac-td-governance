"""Runs the Rego unit tests with OPA if it is installed (CI installs it)."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("opa") is None, reason="opa not installed")
def test_rego_policies():
    proc = subprocess.run(["opa", "test", str(ROOT / "policies"), "-v"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.skipif(shutil.which("conftest") is None, reason="conftest not installed")
def test_conftest_on_fixture_plan(tmp_path):
    from tdgov.policy import evaluate_plan

    res = evaluate_plan(ROOT / "tests" / "fixtures" / "plan_noncompliant.json", ROOT / "policies", ROOT / "policies" / "data")
    codes = sorted({f.split()[0] for f in res.failures})
    assert not res.passed
    assert codes == ["TDGOV-001", "TDGOV-002", "TDGOV-003", "TDGOV-004", "TDGOV-007"]
