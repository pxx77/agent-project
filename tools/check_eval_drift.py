"""Fail when a committed evaluation report no longer matches what the code produces.

``evals/report.json`` is the evidence behind the numbers quoted in each README, so it must not
quietly drift away from the code. This script re-runs the offline evaluation and compares the
deterministic fields of the fresh report against the committed one.

Latency is excluded on purpose: it varies between machines, so including it would make the check
fail for reasons that have nothing to do with correctness. Everything else has to match exactly,
including per-case SQL, row counts and chart specifications.

Usage:
    uv run python tools/check_eval_drift.py citeguard-agent
    uv run python tools/check_eval_drift.py citeguard-agent datapilot-agent

The per-case evaluation is executed through ``uv run`` inside each project directory, so every
project is evaluated with its own virtual environment.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VOLATILE_TOP_LEVEL = {"generated_at"}
VOLATILE_METRICS = {"mean_latency_ms", "p95_latency_ms"}
VOLATILE_CASE_FIELDS = {"latency_ms"}
MAX_REPORTED_DIFFERENCES = 20


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _stable(payload: dict) -> dict:
    """Drop the fields that legitimately vary between runs."""
    trimmed = {key: value for key, value in payload.items() if key not in VOLATILE_TOP_LEVEL}
    if "metrics" in trimmed:
        trimmed["metrics"] = {
            key: value for key, value in trimmed["metrics"].items() if key not in VOLATILE_METRICS
        }
    if "cases" in trimmed:
        trimmed["cases"] = [
            {key: value for key, value in case.items() if key not in VOLATILE_CASE_FIELDS}
            for case in trimmed["cases"]
        ]
    return trimmed


def _differences(committed: dict, fresh: dict) -> list[str]:
    """Describe every difference, so a failure points at the exact field that moved."""
    problems: list[str] = []

    for key in sorted(set(committed) | set(fresh)):
        if key in {"metrics", "cases"}:
            continue
        if committed.get(key) != fresh.get(key):
            problems.append(f"{key}: committed={committed.get(key)!r} fresh={fresh.get(key)!r}")

    committed_metrics = committed.get("metrics", {})
    fresh_metrics = fresh.get("metrics", {})
    for key in sorted(set(committed_metrics) | set(fresh_metrics)):
        if committed_metrics.get(key) != fresh_metrics.get(key):
            problems.append(
                f"metrics.{key}: committed={committed_metrics.get(key)!r} fresh={fresh_metrics.get(key)!r}"
            )

    committed_cases = {case["id"]: case for case in committed.get("cases", [])}
    fresh_cases = {case["id"]: case for case in fresh.get("cases", [])}
    if set(committed_cases) != set(fresh_cases):
        problems.append(
            "case ids: committed-only="
            f"{sorted(set(committed_cases) - set(fresh_cases))} "
            f"fresh-only={sorted(set(fresh_cases) - set(committed_cases))}"
        )
    for case_id in sorted(set(committed_cases) & set(fresh_cases)):
        committed_case = committed_cases[case_id]
        fresh_case = fresh_cases[case_id]
        for key in sorted(set(committed_case) | set(fresh_case)):
            if committed_case.get(key) != fresh_case.get(key):
                problems.append(
                    f"case {case_id}.{key}: committed={committed_case.get(key)!r} fresh={fresh_case.get(key)!r}"
                )
    return problems


def _evaluate(project_dir: Path, output: Path) -> tuple[bool, str]:
    """Run the project's offline evaluation; return (ok, combined output).

    ``--no-sync`` keeps the check read-only: a plain ``uv run`` would re-sync the environment
    against the default extra set and prune ``dev``/``mcp`` from the virtualenv.
    """
    completed = subprocess.run(
        ["uv", "run", "--no-sync", "python", "evals/run_eval.py", "--output", str(output)],
        cwd=project_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.returncode == 0 and output.is_file(), f"{completed.stdout}\n{completed.stderr}"


def _check(project: str) -> bool:
    project_dir = REPO_ROOT / project
    runner = project_dir / "evals" / "run_eval.py"
    committed_path = project_dir / "evals" / "report.json"
    if not runner.is_file() or not committed_path.is_file():
        print(f"SKIP {project}: no evals/run_eval.py or evals/report.json")
        return True

    with tempfile.TemporaryDirectory() as temporary:
        fresh_path = Path(temporary) / "report.json"
        ok, output = _evaluate(project_dir, fresh_path)
        if not ok:
            print(f"FAIL {project}: the offline evaluation did not produce a report")
            print(output.strip()[-2000:])
            return False
        fresh = _stable(_load(fresh_path))

    problems = _differences(_stable(_load(committed_path)), fresh)
    if not problems:
        print(f"PASS {project}: the committed report matches a fresh offline run")
        return True

    print(f"FAIL {project}: {len(problems)} difference(s) between the committed report and a fresh run")
    for problem in problems[:MAX_REPORTED_DIFFERENCES]:
        print(f"  - {problem}")
    if len(problems) > MAX_REPORTED_DIFFERENCES:
        print(f"  ... and {len(problems) - MAX_REPORTED_DIFFERENCES} more")
    print(f"  regenerate with: cd {project} && uv run python evals/run_eval.py")
    return False


def main(argv: list[str]) -> int:
    projects = argv[1:] or ["citeguard-agent", "datapilot-agent"]
    results = [_check(project) for project in projects]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
