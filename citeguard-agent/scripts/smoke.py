from citeguard.workflow import build_fixture_agent

# Index a real bundled fixture rather than the tiny inline smoke document: the documented demo path
# is "upload a document, ask a question, get an answer backed by a citation", and the inline
# paragraph is too short for a question to clear the retrieval relevance floor. The question below
# is taken verbatim from fixtures/test_questions.md for this document.
result = build_fixture_agent(["agent_evaluation_handbook.md"]).run("智能体发布前必须达到哪些评测阈值？")
assert result.citations, "the answer must be backed by at least one retrieved chunk"
assert result.verification.support_rate == 1.0, "every claim in the answer must be supported"
print("CiteGuard smoke: PASS")
