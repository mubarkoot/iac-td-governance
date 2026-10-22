"""Policy-as-code evaluation of Terraform plans with conftest (DR4)."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class PolicyError(Exception):
    pass


@dataclass
class PolicyResult:
    passed: bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    successes: int = 0


def evaluate_plan(plan_json_path: str | Path, policy_dir: str | Path, data_dir: str | Path | None = None,
                  conftest_bin: str = "conftest") -> PolicyResult:
    if shutil.which(conftest_bin) is None:
        raise PolicyError(f"'{conftest_bin}' not found; install conftest (https://www.conftest.dev)")
    args = [conftest_bin, "test", str(plan_json_path), "--policy", str(policy_dir),
            "--namespace", "main", "--output", "json", "--no-color"]
    if data_dir and Path(data_dir).exists():
        args += ["--data", str(data_dir)]
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        raise PolicyError(f"conftest failed: {proc.stderr.strip() or proc.stdout.strip()}")
    try:
        results = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise PolicyError(f"unexpected conftest output: {proc.stdout[:500]}") from exc
    res = PolicyResult(passed=True)
    for r in results:
        res.failures += [f["msg"] for f in (r.get("failures") or [])]
        res.warnings += [w["msg"] for w in (r.get("warnings") or [])]
        res.successes += int(r.get("successes") or 0)
    res.passed = not res.failures
    return res
