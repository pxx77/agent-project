from citeguard.config import Settings
from citeguard.embeddings import HashingEmbedder
from citeguard.models import Chunk
from citeguard.retrieval import (
    BM25Index,
    HybridIndex,
    VectorIndex,
    build_index,
    reciprocal_rank_fusion,
)


def _chunk(chunk_id: str, text: str) -> Chunk:
    document_id = chunk_id.split(":", 1)[0]
    return Chunk(id=chunk_id, document_id=document_id, text=text, start=0, end=len(text))


def test_reciprocal_rank_fusion_ranks_by_the_sum_of_reciprocal_ranks():
    # "b" is second in the lexical list and first in the dense one, so it must beat
    # "a", which only one channel ranked first.
    assert reciprocal_rank_fusion([["a", "b"], ["b", "c"]], limit=3) == ["b", "a", "c"]


def test_reciprocal_rank_fusion_breaks_ties_on_first_seen_order():
    # Both ids carry 1/(k+1), so only the order they were first encountered can separate them.
    assert reciprocal_rank_fusion([["a"], ["b"]], limit=2) == ["a", "b"]


def test_reciprocal_rank_fusion_truncates_to_the_limit():
    assert reciprocal_rank_fusion([["a", "b", "c", "d"]], limit=2) == ["a", "b"]


def test_vector_index_ranks_the_closest_chunk_first():
    chunks = [
        _chunk("far:0", "tomato soup recipe with basil"),
        _chunk("near:0", "incident retention window audit logs"),
    ]
    index = VectorIndex(chunks, HashingEmbedder(512))

    assert index.search("retention audit logs", limit=2)[0].id == "near:0"


def test_vector_index_returns_nothing_for_an_empty_corpus():
    assert VectorIndex([], HashingEmbedder(64)).search("anything") == []


def test_vector_index_returns_nothing_for_a_blank_query():
    index = VectorIndex([_chunk("d:0", "incident retention window")], HashingEmbedder(64))

    assert index.search("   ") == []


def test_vector_index_get_resolves_a_chunk_id():
    chunk = _chunk("d:0", "incident retention window")
    index = VectorIndex([chunk], HashingEmbedder(64))

    assert index.get("d:0") is chunk
    assert index.get("missing") is None


def test_hybrid_index_refuses_when_the_lexical_floor_finds_nothing():
    # The dense channel alone answers everything, so the refusal has to come from the
    # lexical floor; this pins that the fusion does not override it.
    chunks = [_chunk("d:0", "故障等级分为 p0、p1 和 p2。")]
    index = HybridIndex(chunks, HashingEmbedder(64))

    assert index.search("如何用慢炖锅制作番茄汤并分为几个步骤") == []


def test_hybrid_augment_still_refuses_unrelated_questions():
    chunks = [_chunk("d:0", "故障等级分为 p0、p1 和 p2。")]
    index = HybridIndex(chunks, HashingEmbedder(64), admit_dense_only=True)

    assert index.search("如何用慢炖锅制作番茄汤并分为几个步骤") == []


def test_hybrid_index_reranks_only_within_the_lexical_candidates_by_default():
    chunks = [
        _chunk("a:0", "retention window audit logs are kept"),
        _chunk("b:0", "incident severity determines the retention window"),
        _chunk("c:0", "gardening tips for growing tomatoes"),
    ]
    lexical = {chunk.id for chunk in BM25Index(chunks).search("retention window", 3)}
    index = HybridIndex(chunks, HashingEmbedder(128), min_coverage=0.0)

    assert {chunk.id for chunk in index.search("retention window", 3)} <= lexical


def test_hybrid_index_get_resolves_a_chunk_id():
    chunk = _chunk("d:0", "incident retention window")
    index = HybridIndex([chunk], HashingEmbedder(64))

    assert index.get("d:0") is chunk
    assert index.get("missing") is None


def test_retrievers_expose_a_name_that_reaches_the_trace():
    chunks = [_chunk("d:0", "incident retention window audit logs")]

    assert BM25Index(chunks).name == "bm25"
    assert VectorIndex(chunks, HashingEmbedder(64)).name == "vector"
    assert HybridIndex(chunks, HashingEmbedder(64)).name == "hybrid"


def test_build_index_honours_the_explicit_lexical_mode():
    chunks = [_chunk("d:0", "incident retention window")]

    assert isinstance(build_index(chunks, Settings(retriever="lexical")), BM25Index)


def test_build_index_builds_the_hybrid_index_when_asked_directly():
    chunks = [_chunk("d:0", "incident retention window")]

    built = build_index(chunks, Settings(retriever="hybrid", embedding_dim=64))

    assert isinstance(built, HybridIndex)


def test_build_index_auto_stays_lexical_for_the_offline_hashing_encoder():
    # The bundled encoder is lexical, so fusing it would only perturb an already correct
    # ranking; evals/retrieval_ablation.json records the measurement behind this default.
    chunks = [_chunk("d:0", "incident retention window")]

    assert isinstance(build_index(chunks, Settings(retriever="auto", embedding_provider="hashing")), BM25Index)


def test_build_index_auto_uses_hybrid_for_a_real_embedding_endpoint():
    # No chunks means no embedding request, so this stays offline while still proving
    # that a genuine semantic provider is what switches fusion on.
    settings = Settings(
        retriever="auto",
        embedding_provider="http",
        embedding_base_url="https://example.invalid/v1",
        embedding_model="some-embedding-model",
    )

    assert isinstance(build_index([], settings), HybridIndex)


def test_build_index_falls_back_to_lexical_when_the_dense_channel_is_off():
    chunks = [_chunk("d:0", "incident retention window")]

    built = build_index(chunks, Settings(retriever="hybrid", embedding_provider="off"))

    assert isinstance(built, BM25Index)
