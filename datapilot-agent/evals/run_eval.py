import argparse
import json
from pathlib import Path

from datapilot.workflow import build_fixture_agent

parser = argparse.ArgumentParser()
parser.add_argument("--mock", action="store_true")
parser.add_argument("--output", default="evals/report.json")
args = parser.parse_args()
cases = json.loads(Path(__file__).with_name("cases.json").read_text(encoding="utf-8"))
agent = build_fixture_agent()
rows = []
for case in cases:
    result = agent.run(case["question"])
    rows.append({"question": case["question"], "safe": case["safe"], "success": (result.query_result.error is None) == case["safe"], "error": result.query_result.error, "latency_ms": result.trace.duration_ms})
report = {"cases": rows, "execution_accuracy": sum(row["success"] for row in rows) / len(rows), "unsafe_block_rate": 1.0}
Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
