"""
M3 RAG 管道测试

运行方式（项目根目录）：
    python -m pytest backend/tests/ -v

覆盖：切块逻辑（纯函数）+ 入库幂等 + BM25 索引检索 + 向量检索命中 + 路由分支
说明：ingest/向量检索用例加载真实 bge 模型（已本地缓存，进程内单例只加载一次）
"""
from pathlib import Path

import backend.rag.ingest as ingest
import backend.rag.search as search


# ===== 切块（纯函数，不依赖模型/数据库）=====

class TestChunkMarkdown:
    def test_sections_by_heading(self):
        """按标题分节 + 标题路径正确"""
        md = "# 顶层\n引言内容。\n## 节A\nA 的内容。\n## 节B\nB 的内容。\n### 节B子节\n子内容。"
        chunks = ingest.chunk_markdown(md)
        titles = [c["title"] for c in chunks]
        assert titles == ["顶层", "顶层/节A", "顶层/节B", "节B/节B子节"]
        assert "A 的内容" in chunks[1]["text"]
        assert chunks[1]["text"].startswith("顶层/节A")  # 块文本带标题前缀

    def test_no_heading_single_chunk(self):
        """无标题文档整体一块"""
        chunks = ingest.chunk_markdown("只有一段正文，没有标题。" * 10)
        assert len(chunks) == 1
        assert chunks[0]["title"] == ""

    def test_long_body_hard_split(self):
        """超长正文按窗口硬切，块数 > 1 且带（续）标记的标题前缀"""
        body = "中文内容循环填充。" * 200  # 1600 字 > MAX 500
        md = f"# 长文\n{body}"
        chunks = ingest.chunk_markdown(md)
        assert len(chunks) >= 3
        assert all(c["title"] == "长文" for c in chunks)
        assert all(len(c["text"]) <= ingest.MAX_CHUNK_CHARS + 20 for c in chunks)

    def test_short_tail_merge(self):
        """相邻同节过短碎片合并为一块"""
        md = "# 节\n短句一。短句二。"
        chunks = ingest.chunk_markdown(md)
        assert len(chunks) == 1

    def test_offset_monotonic(self):
        """块 offset 单调不减（源文件字符偏移）"""
        md = "# A\n内容。\n## B\n更多内容。"
        offsets = [c["offset"] for c in ingest.chunk_markdown(md)]
        assert offsets == sorted(offsets)


# ===== 入库（真实 bge，临时 chroma）=====

class TestIngest:
    def _write_raw(self, rag_env, name="doc.md",
                   text="# 测试\nFastAPI 是高性能 Web 框架。\n## 坑\n连接超时的根因是权限。"):
        f = Path(rag_env["raw"]) / name
        f.write_text(text, encoding="utf-8")
        return f

    def test_ingest_and_count(self, rag_env):
        f = self._write_raw(rag_env)
        n = ingest.ingest_file(f)
        assert n > 0
        assert ingest.get_collection(ingest.RAW_COLLECTION).count() == n

    def test_reingest_idempotent(self, rag_env):
        """重复入库不产生重复块（先删后写）"""
        f = self._write_raw(rag_env)
        ingest.ingest_file(f)
        ingest.ingest_file(f)  # 第二次
        n_chunks = len(ingest.chunk_markdown(f.read_text(encoding="utf-8")))
        assert ingest.get_collection(ingest.RAW_COLLECTION).count() == n_chunks

    def test_metadata_fields(self, rag_env):
        f = self._write_raw(rag_env)
        ingest.ingest_file(f)
        got = ingest.get_collection(ingest.RAW_COLLECTION).get()
        meta = got["metadatas"][0]
        assert meta["source"] == "doc.md"
        assert "title" in meta and "offset" in meta and "mtime" in meta

    def test_delete_source_chunks(self, rag_env):
        f = self._write_raw(rag_env)
        ingest.ingest_file(f)
        ingest.delete_source_chunks("doc.md")
        assert ingest.get_collection(ingest.RAW_COLLECTION).count() == 0


# ===== BM25（真实 wiki 文件，不依赖模型）=====

class TestBM25:
    def test_build_and_search(self, rag_env):
        """3 篇语料（N<3 时 BM25Okapi 的 IDF 恒 ≤0，分数必为 0，这是数学边界）"""
        (Path(rag_env["wiki"]) / "fastapi.md").write_text(
            "# FastAPI\nFastAPI 双阶段 tool-calling 循环模式。", encoding="utf-8")
        (Path(rag_env["wiki"]) / "fsrs.md").write_text(
            "# FSRS\n间隔重复复习算法，ease 从 2.5 起步，逐步拉长间隔。", encoding="utf-8")
        (Path(rag_env["wiki"]) / "other.md").write_text(
            "# 其他\n这里是一段完全无关的填充内容，用来稀释语料提升 IDF。", encoding="utf-8")
        n = search.bm25_index.build(rag_env["wiki"])
        assert n == 3
        hits = search.bm25_index.search("双阶段 tool-calling")
        assert hits and hits[0]["path"] == "fastapi.md"
        assert hits[0]["score"] > 0

    def test_empty_wiki_returns_empty(self, rag_env):
        search.bm25_index.build(rag_env["wiki"])
        assert search.bm25_index.search("任何查询") == []


# ===== 检索路由 =====

class TestSearchKnowledge:
    def test_vector_fallback_when_wiki_empty(self, rag_env):
        """wiki 空 → BM25 无命中 → 向量兜底，结果来自 raw"""
        f = Path(rag_env["raw"]) / "raw1.md"
        f.write_text("# ChromaDB\nChromaDB 连接超时的根因是目录权限不足。",
                     encoding="utf-8")
        ingest.ingest_file(f)
        out = search.search_knowledge("ChromaDB 连接超时")
        assert out["route"] == "vector_fallback"
        assert out["results"]
        assert out["results"][0]["kind"] == "raw"
        assert "超时" in out["results"][0]["text"] or "ChromaDB" in out["results"][0]["text"]

    def test_bm25_strong_route(self, rag_env):
        """wiki 强命中 → 直接走 BM25，不再查向量（语料 3 篇保证正 IDF，
        目标文档查询词高频 + 标题加权 ×3 + 短文档长度归一化优势）"""
        (Path(rag_env["wiki"]) / "timeout.md").write_text(
            "# 连接超时 连接超时\n连接超时 连接超时 连接超时 连接超时的排查步骤。",
            encoding="utf-8")
        (Path(rag_env["wiki"]) / "filler1.md").write_text(
            "# 无关一\n" + "这一段与查询毫无关系的长填充内容。" * 20, encoding="utf-8")
        (Path(rag_env["wiki"]) / "filler2.md").write_text(
            "# 无关二\n" + "另一段同样不相关的填充文本而已。" * 20, encoding="utf-8")
        search.bm25_index.build(rag_env["wiki"])
        out = search.search_knowledge("连接超时")
        assert out["route"] == "wiki_bm25"
        assert out["results"][0]["source"] == "timeout.md"
        assert out["results"][0]["score"] >= search.BM25_THRESHOLD

    def test_results_carry_source(self, rag_env):
        """所有结果必须带来源字段（答案强制附引用的依据）"""
        f = Path(rag_env["raw"]) / "raw2.md"
        f.write_text("# 向量库\n向量检索用 bge 中文模型。", encoding="utf-8")
        ingest.ingest_file(f)
        out = search.search_knowledge("向量检索用什么模型")
        for r in out["results"]:
            assert "source" in r and "score" in r and "text" in r
