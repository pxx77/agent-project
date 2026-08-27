from citeguard.ingestion import chunk_document
from citeguard.models import Document


def test_chunk_ids_are_stable():
    chunks = chunk_document(Document(id="d1", name="x.md", text="alpha " * 300), size=40, overlap=5)
    assert chunks[0].id == "d1:0"
    assert chunks[1].document_id == "d1"
