from datapilot.workflow import build_fixture_agent


def test_mock_agent_answers_fixture_question():
    result = build_fixture_agent().run("What is revenue by region?")
    assert result.query_result.error is None
    assert "region" in result.query_result.columns
    assert result.verification.consistent is True
