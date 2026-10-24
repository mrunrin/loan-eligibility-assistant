"""Hosted embedding / rerank clients: request shape and response mapping (no network)."""

from app import retrieval


def test_jina_embeddings_use_task_adapters_and_keep_input_order(monkeypatch):
    calls = []

    def fake_post(url, api_key, payload):
        calls.append(payload)
        # API may return items out of order; each carries its input index.
        return {"data": [{"index": i, "embedding": [float(i)]} for i in reversed(range(len(payload["input"])))]}

    monkeypatch.setattr(retrieval, "_post_with_retry", fake_post)
    emb = retrieval.JinaEmbeddings(model="jina-embeddings-v3", api_key="k")

    assert emb.embed_documents(["a", "b", "c"]) == [[0.0], [1.0], [2.0]]
    assert emb.embed_query("q") == [0.0]
    assert [c["task"] for c in calls] == ["retrieval.passage", "retrieval.query"]


def test_cohere_scores_map_back_to_input_order(monkeypatch):
    monkeypatch.setattr(retrieval, "COHERE_API_KEY", "k")
    monkeypatch.setattr(
        retrieval, "_post_with_retry",
        lambda url, key, payload: {"results": [{"index": 2, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.1}]},
    )
    assert retrieval._cohere_scores("q", ["x", "y", "z"]) == [0.1, 0.0, 0.9]
