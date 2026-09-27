import os
import subprocess
import sys

import pytest

from citeguard.config import Settings
from citeguard.embeddings import HashingEmbedder, HttpEmbedder, build_embedder, cosine


def test_hashing_embedder_is_deterministic_for_the_same_text():
    left = HashingEmbedder(128).embed(["智能体评测需要引用证据"])[0]
    right = HashingEmbedder(128).embed(["智能体评测需要引用证据"])[0]

    assert left == right
    assert left


def test_hashing_embedder_is_stable_across_processes():
    """A per-process random seed would make the committed report unreproducible.

    ``hashlib`` is used instead of the builtin ``hash`` precisely for this, so the check
    runs the encoder under three different ``PYTHONHASHSEED`` values and requires one answer.
    """
    script = (
        "import json;"
        "from citeguard.embeddings import HashingEmbedder;"
        "print(json.dumps(sorted(HashingEmbedder(64).embed(['智能体评测 agent evaluation'])[0].items())))"
    )
    outputs = []
    for seed in ("0", "1", "random"):
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        assert completed.returncode == 0, completed.stderr
        outputs.append(completed.stdout.strip())

    assert len(set(outputs)) == 1


def test_hashing_embedder_returns_unit_length_vectors():
    vector = HashingEmbedder(256).embed(["retention window audit logs 保留审计日志"])[0]

    assert cosine(vector, vector) == pytest.approx(1.0)


def test_hashing_embedder_maps_empty_text_to_an_empty_vector():
    assert HashingEmbedder(64).embed([""])[0] == {}
    assert HashingEmbedder(64).embed(["   "])[0] == {}


def test_hashing_embedder_enforces_a_minimum_dimension():
    # A tiny dim would collide most features into one bucket; the floor keeps the
    # encoder usable when a caller passes a small value by mistake.
    assert HashingEmbedder(4).dim == 32
    assert HashingEmbedder(1024).dim == 1024


def test_cosine_is_higher_for_overlapping_text_than_for_unrelated_text():
    query = HashingEmbedder(512).embed(["incident retention window"])[0]
    near = HashingEmbedder(512).embed(["incident retention window is ninety days"])[0]
    far = HashingEmbedder(512).embed(["tomato soup recipe"])[0]

    assert cosine(query, near) > cosine(query, far)


def test_cosine_of_disjoint_features_is_zero():
    assert cosine({0: 1.0}, {1: 1.0}) == 0.0


def test_build_embedder_returns_none_when_the_dense_channel_is_off():
    assert build_embedder(Settings(embedding_provider="off")) is None


def test_build_embedder_defaults_to_the_offline_hashing_encoder():
    embedder = build_embedder(Settings(embedding_provider="hashing", embedding_dim=64))

    assert isinstance(embedder, HashingEmbedder)
    assert embedder.dim == 64


def test_build_embedder_requires_an_endpoint_and_model_for_http():
    with pytest.raises(ValueError):
        build_embedder(Settings(embedding_provider="http"))


def test_build_embedder_builds_the_http_client_when_configured():
    embedder = build_embedder(
        Settings(
            embedding_provider="http",
            embedding_base_url="https://example.invalid/v1",
            embedding_model="some-embedding-model",
        )
    )

    assert isinstance(embedder, HttpEmbedder)
    assert embedder.base_url == "https://example.invalid/v1"
