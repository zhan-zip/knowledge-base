"""pytest 共享 fixture：临时数据库 + RAG 测试环境"""
import pytest

import backend.rag.ingest as ingest
import backend.rag.search as search
import backend.storage.db as db


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """把 DB_PATH 指向临时文件并建表，每个测试独享一个库"""
    db_path = tmp_path / "test_kb.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    db.init_db()
    return str(db_path)


@pytest.fixture()
def rag_env(tmp_path, monkeypatch):
    """临时 ChromaDB + raw/wiki 目录，重置模块级单例（bge 模型缓存保留，进程内只加载一次）"""
    chroma_dir = tmp_path / "chroma"
    raw_dir = tmp_path / "raw"
    wiki_dir = tmp_path / "wiki"
    raw_dir.mkdir()
    wiki_dir.mkdir()
    monkeypatch.setattr(ingest, "CHROMA_DIR", chroma_dir)
    monkeypatch.setattr(ingest, "RAW_DIR", raw_dir)
    monkeypatch.setattr(ingest, "_client", None)  # 重置 PersistentClient 单例
    monkeypatch.setattr(search, "WIKI_DIR", wiki_dir)
    monkeypatch.setattr(search, "bm25_index", search.BM25WikiIndex())
    return {"chroma": chroma_dir, "raw": raw_dir, "wiki": wiki_dir}
