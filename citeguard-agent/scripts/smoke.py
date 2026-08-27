from citeguard.workflow import build_fixture_agent

result = build_fixture_agent().run("What does the document say about evaluation?")
assert result.citations and result.verification.support_rate == 1.0
print("CiteGuard smoke: PASS")
