from src.corpus import REQUIRED_METADATA, is_lfs_pointer, load_demo_corpus
from src.ingest import build_langchain_documents
from src.settings import Settings


def test_demo_corpus_has_required_metadata() -> None:
    documents = load_demo_corpus()

    assert len(documents) >= 3
    for document in documents:
        assert REQUIRED_METADATA.issubset(document.metadata)
        assert document.text


def test_chunks_keep_source_metadata() -> None:
    settings = Settings(chunk_size=350, chunk_overlap=50, include_task_data=False)
    chunks = build_langchain_documents(settings)

    assert chunks
    for chunk in chunks:
        assert chunk.metadata["source_id"].startswith("DOC-")
        assert chunk.metadata["title"]
        assert "chunk_index" in chunk.metadata
        assert chunk.page_content.strip()


def test_is_lfs_pointer_detects_git_lfs_stub(tmp_path) -> None:
    path = tmp_path / "fake.xlsx"
    path.write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 100\n",
        encoding="utf-8",
    )

    assert is_lfs_pointer(path) is True

    real = tmp_path / "real.bin"
    real.write_bytes(b"PK" + b"\x00" * 200)

    assert is_lfs_pointer(real) is False
