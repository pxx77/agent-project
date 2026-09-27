"""Check a running DataPilot container through its public HTTP API.

CI builds the image, starts it with the image's own default environment, and runs this against the
published port. Every assertion goes over HTTP rather than in-process, so the check fails if the
image is missing the fixtures the app loads at import time, if the API listens on a port other than
the documented one, or if the container turns out to need credentials it was never given.

    python scripts/container_smoke.py --base-url http://127.0.0.1:8766
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sales.csv"
QUESTION = "What is revenue by region?"
EXPECTED_TOTALS = {"West": 3200.0, "South": 3050.0, "East": 2800.0, "North": 2250.0}


def check(condition: bool, description: str, detail: str = "") -> None:
    if not condition:
        raise SystemExit(f"FAIL: {description}" + (f" [{detail}]" if detail else ""))
    print(f"ok: {description}")


def wait_for_health(client: httpx.Client, base_url: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    last_error = "no attempt was made"
    while time.monotonic() < deadline:
        try:
            response = client.get(f"{base_url}/health")
            if response.status_code == 200:
                return response.json()
            last_error = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.5)
    raise SystemExit(f"FAIL: {base_url}/health was not ready within {timeout:g}s [{last_error}]")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    with httpx.Client(timeout=30.0) as client:
        health = wait_for_health(client, base_url, args.timeout)
        check(health.get("status") == "ok", "/health reports status ok", str(health))
        check(health.get("mock") is True, "the image runs with no API key present", str(health))
        check(health.get("provider") == "mock", "/health reports the offline provider", str(health))

        with FIXTURE.open("rb") as handle:
            profile = client.post(
                f"{base_url}/profile",
                files={"file": (FIXTURE.name, handle, "text/csv")},
            )
        check(
            profile.status_code == 200,
            "POST /profile accepts the bundled fixture",
            f"HTTP {profile.status_code}: {profile.text[:300]}",
        )
        tables = profile.json().get("tables", {})
        check("sales" in tables, "the upload is profiled as table sales", str(list(tables)))
        sales = tables["sales"]
        check(sales.get("row_count") == 12, "sales.csv is profiled as 12 rows", str(sales.get("row_count")))
        check(
            len(sales.get("columns", [])) == 3,
            "sales.csv is profiled as 3 columns",
            str([column.get("name") for column in sales.get("columns", [])]),
        )

        answer = client.post(f"{base_url}/ask", json={"question": QUESTION})
        check(
            answer.status_code == 200,
            "POST /ask returns a result",
            f"HTTP {answer.status_code}: {answer.text[:300]}",
        )
        payload = answer.json()

    sql = str(payload.get("sql", ""))
    check(sql.lstrip().upper().startswith("SELECT"), "the generated statement is read-only", sql)

    result = payload["query_result"]
    check(result.get("error") is None, "the query executed without error", str(result.get("error")))
    check(result.get("policy_blocked") is False, "the read-only policy allowed the statement", str(result.get("policy_blocked")))
    check(
        result.get("columns") == ["region", "total_revenue"],
        "the result carries the expected columns",
        str(result.get("columns")),
    )
    check(result.get("row_count") == 4, "the aggregation returned one row per region", str(result.get("row_count")))
    totals = {row[0]: float(row[1]) for row in result.get("rows", [])}
    check(totals == EXPECTED_TOTALS, "the per-region totals match the fixture", str(totals))
    check(payload["verification"]["consistent"] is True, "the result is marked verified", str(payload["verification"]))
    check((payload.get("chart") or {}).get("kind") == "bar", "the result came back with a bar chart spec", str(payload.get("chart")))

    print(f"PASS: container smoke against {base_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
