from datapilot.workflow import build_fixture_agent

result = build_fixture_agent().run("What is revenue by region?")
assert result.query_result.error is None and result.verification.consistent
print("DataPilot smoke: PASS")
