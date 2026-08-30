"""
M5 Agent 工具执行器测试（mock LLM，不真调 API）

运行方式（项目根目录）：
    python -m pytest backend/tests/ -v

真实 LLM 的 chat 端到端冒烟走 CLI/HTTP（见实施计划测试记录），不进 pytest
"""
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.agent.tools as tools
import backend.agent.llm as agent_llm
import backend.compiler.compile as compile_mod
import backend.compiler.lint as lint
import backend.rag.ingest as ingest
import backend.rag.search as search
import backend.storage.db as db
from backend.compiler.schema import SummaryOutput


@pytest.fixture()
def agent_env(tmp_path, monkeypatch):
    """agent 全家桶临时环境：raw/wiki/learning/chroma/db 全隔离 + 单例重置"""
    raw = tmp_path / "raw"; raw.mkdir()
    wiki = tmp_path / "wiki"
    for sub in ("summaries", "concepts", "bugs", "topics"):
        (wiki / sub).mkdir(parents=True)
    learning = tmp_path / "learning"; (learning / "flashcards").mkdir(parents=True)
    chroma = tmp_path / "chroma"

    monkeypatch.setattr(tools, "RAW_DIR", raw)
    monkeypatch.setattr(tools, "WIKI_DIR", wiki)
    monkeypatch.setattr(compile_mod, "RAW_DIR", raw)
    monkeypatch.setattr(compile_mod, "WIKI_DIR", wiki)
    monkeypatch.setattr(compile_mod, "LEARNING_DIR", learning)
    monkeypatch.setattr(compile_mod, "FLASHCARDS_DIR", learning / "flashcards")
    monkeypatch.setattr(lint, "WIKI_DIR", wiki)  # compile_all 内部调 lint，必须同源
    monkeypatch.setattr(ingest, "CHROMA_DIR", chroma)
    monkeypatch.setattr(ingest, "RAW_DIR", raw)
    monkeypatch.setattr(ingest, "_client", None)
    monkeypatch.setattr(search, "WIKI_DIR", wiki)
    monkeypatch.setattr(search, "bm25_index", search.BM25WikiIndex())
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "kb.db"))
    db.init_db()
    return {"raw": raw, "wiki": wiki, "tmp": tmp_path}


def _fake_llm_response(content: str):
    """构造 chat_completion 返回结构的替身"""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


# ===== search_knowledge =====

class TestSearchTool:
    def test_returns_sources(self, agent_env):
        f = agent_env["wiki"] / "concepts" / "fastapi.md"
        f.write_text("---\ntitle: FastAPI\ntype: concept\ntopic: Web\n"
                     "sources: [raw/a.md]\nrelated: []\ntags: []\n"
                     "created: 2026-08-30\n---\n\nFastAPI 是高性能框架。"
                     "双阶段 tool-calling 循环。", encoding="utf-8")
        ingest.ingest_file(f, ingest.WIKI_COLLECTION, agent_env["wiki"])  # 向量入库
        search.bm25_index.build(agent_env["wiki"])
        # 语料仅 1 篇 → BM25 分数为 0 → 走向量兜底（bge 模型进程内已缓存）
        out = json.loads(tools._tool_search_knowledge("FastAPI 高性能"))
        assert "results" in out and out["results"]
        assert all("source" in r for r in out["results"])

    def test_empty_library(self, agent_env):
        out = json.loads(tools._tool_search_knowledge("任何词"))
        assert out["results"] == [] or out["route"] == "vector_fallback"


# ===== get_due_reviews =====

class TestDueReviewsTool:
    def test_no_due(self, agent_env):
        result = tools._tool_get_due_reviews()
        assert "没有到期" in result

    def test_with_due_cards(self, agent_env):
        today = date.today().isoformat()
        db.upsert_review("fastapi", today, 1.0, 2.5)
        db.add_flashcard("fastapi", "FastAPI 是什么？", "高性能 Web 框架")
        out = json.loads(tools._tool_get_due_reviews())
        assert out["due_count"] == 1
        assert out["cards"][0]["card_fronts"] == ["FastAPI 是什么？"]


# ===== generate_flashcard（mock LLM）=====

class TestGenerateFlashcard:
    def test_by_slug(self, agent_env, monkeypatch):
        page = agent_env["wiki"] / "concepts" / "fsrs.md"
        page.write_text("---\ntitle: FSRS\ntype: concept\ntopic: 复习\n"
                        "sources: [raw/a.md]\nrelated: []\ntags: []\n"
                        "created: 2026-08-30\n---\n\n间隔重复算法。", encoding="utf-8")
        # _tool_generate_flashcard 内部函数级 import，须 patch 源头模块属性
        monkeypatch.setattr(
            agent_llm, "get_llm_client",
            lambda: SimpleNamespace(chat_completion=_fake_chat(
                '{"front": "FSRS 的 ease 初始值？", "back": "2.5"}')))
        result = asyncio_run(tools._tool_generate_flashcard("fsrs"))
        assert "已为概念" in result
        cards = db.list_flashcards()
        assert len(cards) == 1 and "ease" in cards[0]["front"]
        assert db.get_review("fsrs")["ease"] == 2.5

    def test_by_title_fallback(self, agent_env, monkeypatch):
        page = agent_env["wiki"] / "concepts" / "bm25.md"
        page.write_text("---\ntitle: BM25 检索\ntype: concept\ntopic: 检索\n"
                        "sources: [raw/a.md]\nrelated: []\ntags: []\n"
                        "created: 2026-08-30\n---\n\n词频检索算法。", encoding="utf-8")
        monkeypatch.setattr(
            agent_llm, "get_llm_client",
            lambda: SimpleNamespace(chat_completion=_fake_chat(
                '{"front": "BM25 是？", "back": "词频检索"}')))
        result = asyncio_run(tools._tool_generate_flashcard("BM25 检索"))
        assert "已为概念" in result
        assert db.list_flashcards()[0]["concept"] == "bm25"

    def test_missing_concept(self, agent_env):
        result = asyncio_run(tools._tool_generate_flashcard("不存在的概念"))
        assert "未找到概念" in result


def _fake_chat(content):
    async def _inner(**kwargs):
        return _fake_llm_response(content)
    return _inner


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


# ===== ingest_source / compile_wiki（mock LLM 编译）=====

def _patch_compile_llm(monkeypatch, nom_summary):
    """把 compile 的 LLM 调用替换为固定输出（不真调 API）"""
    async def fake_call(raw_text):
        return nom_summary
    monkeypatch.setattr(compile_mod, "_call_llm_json", fake_call)


class TestIngestAndCompile:
    def test_ingest_from_project_path(self, agent_env, monkeypatch):
        """项目内其他位置的文件 → 复制进 raw/ → 编译"""
        _patch_compile_llm(monkeypatch, SummaryOutput(
            summary="摘要", topics=["T"], concepts=[], bugs=[]))
        src = agent_env["tmp"] / "outside.md"
        src.write_text("# 外部资料\n内容。", encoding="utf-8")
        result = asyncio_run(tools._tool_ingest_source(str(src)))
        assert "已收录" in result and "compiled" in result
        assert (agent_env["raw"] / "outside.md").exists()

    def test_ingest_missing_file(self, agent_env):
        result = asyncio_run(tools._tool_ingest_source("不存在.md"))
        assert "文件不存在" in result

    def test_compile_wiki_tool(self, agent_env, monkeypatch):
        f = agent_env["raw"] / "a.md"
        f.write_text("# 资料\n正文。", encoding="utf-8")
        _patch_compile_llm(monkeypatch, SummaryOutput(
            summary="摘要", topics=[], concepts=[], bugs=[]))
        result = asyncio_run(tools._tool_compile_wiki())
        assert "新增编译 1 个" in result
        assert (agent_env["wiki"] / "_index.md").exists()  # lint 重建过


# ===== execute_tool 分发 =====

class TestExecuteTool:
    def test_unknown_tool(self, agent_env):
        import asyncio
        result = asyncio.run(tools.execute_tool("no_such_tool", {}))
        assert "未知工具" in result

    def test_error_becomes_text(self, agent_env, monkeypatch):
        """工具内异常 → 返回错误文本给 LLM，不向调用方抛出"""
        def boom(**kwargs):
            raise RuntimeError("爆炸了")
        monkeypatch.setattr(tools, "_tool_search_knowledge", boom)
        import asyncio
        result = asyncio.run(tools.execute_tool("search_knowledge", {"query": "x"}))
        assert "执行失败" in result and "爆炸了" in result
