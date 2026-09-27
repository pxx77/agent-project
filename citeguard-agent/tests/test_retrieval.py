from citeguard.models import Chunk
from citeguard.retrieval import BM25Index, terms, tokenize


def test_bm25_returns_evidence_for_query():
    index = BM25Index([Chunk(id="d:0", document_id="d", text="retrieval augmented generation", start=0, end=32)])
    assert index.search("retrieval", 1)[0].id == "d:0"


def test_retrieval_supports_chinese_documents_and_questions():
    index = BM25Index(
        [Chunk(id="cn:0", document_id="cn", text="智能体评测需要检查任务成功率和引用证据。", start=0, end=22)]
    )

    assert index.search("智能体评测有哪些要求？", 1)[0].id == "cn:0"


def test_tokenize_splits_chinese_into_overlapping_bigrams_and_keeps_repeats():
    assert tokenize("智能体") == ["智能", "能体"]
    assert tokenize("Agent agent") == ["agent", "agent"]


def test_terms_stays_a_deduplicated_set_for_verification():
    assert terms("Agent agent 智能体") == {"agent", "智能", "能体"}


def test_search_without_matching_terms_returns_nothing():
    index = BM25Index([Chunk(id="d:0", document_id="d", text="retrieval augmented generation", start=0, end=32)])

    assert index.search("kubernetes") == []


def test_search_drops_chunks_that_only_share_a_stray_term():
    # The chunk shares just one of the question's terms ("分为"), and a question with three
    # or more terms needs at least two shared terms, so the agent must report insufficient
    # evidence instead of answering from a coincidence.
    index = BM25Index([Chunk(id="d:0", document_id="d", text="故障等级分为 p0、p1 和 p2。", start=0, end=1)])

    assert index.search("如何用慢炖锅制作番茄汤并分为几个步骤") == []


def test_search_keeps_a_chunk_that_covers_the_question():
    index = BM25Index([Chunk(id="d:0", document_id="d", text="故障等级分为 p0、p1 和 p2。", start=0, end=1)])

    assert index.search("故障等级分为哪几级？", limit=1)[0].id == "d:0"


def test_search_ignores_out_of_corpus_terms_when_measuring_coverage():
    # Only "复盘" and "提交" exist in the corpus, and the chunk has both, so the question is
    # relevant no matter how many words of it the documents never mention.
    index = BM25Index(
        [Chunk(id="d:0", document_id="d", text="复盘必须在两个工作日内提交，值班人员负责。", start=0, end=1)]
    )

    assert index.search("请参照外部行业标准和国际惯例，说明复盘提交的时限要求是什么？", limit=1)[0].id == "d:0"


def test_search_drops_a_chunk_whose_shared_terms_are_too_generic():
    # The first chunk shares two terms with the question, but both are so common that they
    # cover almost none of the question's IDF mass; the rare chunk is the real answer.
    chunks = [
        Chunk(id=f"common:{index}", document_id="common", text="common1 common2", start=0, end=1)
        for index in range(8)
    ]
    chunks.append(Chunk(id="rare:0", document_id="rare", text="rare1 rare2 rare3", start=0, end=1))
    index = BM25Index(chunks)

    assert [chunk.id for chunk in index.search("rare1 rare2 rare3 common1 common2")] == ["rare:0"]


def test_bm25_weights_a_rare_term_above_a_common_one():
    # Every "common" chunk matches the shared term once, so plain overlap counting
    # would tie and fall back to document order instead of surfacing the rare hit.
    chunks = [
        Chunk(id=f"common:{index}", document_id="common", text="shared topic about reporting metrics", start=0, end=1)
        for index in range(6)
    ]
    chunks.append(Chunk(id="rare:0", document_id="rare", text="quixotic", start=0, end=1))
    index = BM25Index(chunks)

    hits = index.search("reporting quixotic", limit=2)

    assert hits[0].id == "rare:0"


def test_bm25_normalizes_by_document_length():
    short = Chunk(id="short", document_id="s", text="budget", start=0, end=1)
    filler = " ".join(f"filler{position}" for position in range(20))
    long_chunk = Chunk(id="long", document_id="l", text=f"budget {filler}", start=0, end=1)
    # The long chunk comes first, so overlap counting would prefer it on a tie.
    index = BM25Index([long_chunk, short])

    assert index.search("budget", limit=2)[0].id == "short"


def test_repeated_query_terms_score_higher_than_a_single_mention():
    once = Chunk(id="once", document_id="a", text="budget planning cycle", start=0, end=1)
    twice = Chunk(id="twice", document_id="b", text="budget review budget planning", start=0, end=1)
    index = BM25Index([once, twice])

    assert index.search("budget", limit=2)[0].id == "twice"
