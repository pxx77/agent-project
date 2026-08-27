from citeguard.workflow import build_fixture_agent


def test_mock_workflow_returns_resolvable_citation():
    result = build_fixture_agent().run("What does the fixture say about evaluation?")
    assert result.citations
    assert all(c in result.trace.retrieved_chunk_ids for c in result.citations)
    assert result.verification.supported_claims >= 1


def test_empty_retrieval_is_explicit():
    result = build_fixture_agent().run("unrelated phrase xyz")
    assert "insufficient" in result.answer.lower()
