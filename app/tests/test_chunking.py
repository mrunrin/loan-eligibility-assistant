from app import chunking


def test_md_frontmatter_is_not_indexed(tmp_path, monkeypatch):
    (tmp_path / "doc.md").write_text(
        "---\ndoc_id: x\nsource_url: https://example.org\n---\n\n# Title\n\nPenal charges must be disclosed.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(chunking, "DATA_DIR", tmp_path)

    docs = chunking._build_md_documents()

    text = "\n".join(d.page_content for d in docs)
    assert "doc_id" not in text and "source_url" not in text
    assert "Penal charges must be disclosed." in text
    assert docs[0].metadata["section"] == "# Title"
