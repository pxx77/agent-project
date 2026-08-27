from citeguard.models import Chunk
from citeguard.retrieval import BM25Index


def test_bm25_returns_evidence_for_query():
    index = BM25Index([Chunk(id="d:0", document_id="d", text="retrieval augmented generation", start=0, end=32)])
    assert index.search("retrieval", 1)[0].id == "d:0"
