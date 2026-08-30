"""
M2 数据层冒烟测试

运行方式（项目根目录）：
    python -m pytest backend/tests/ -v

覆盖：建表幂等性 + 五张表的 insert/query/update/delete 全链路
"""
import sqlite3

import backend.storage.db as db


# ===== 建表 =====

class TestInitDb:
    def test_init_db_idempotent(self, temp_db):
        """重复初始化不报错（幂等）"""
        db.init_db()  # 第二次调用
        conn = sqlite3.connect(temp_db)
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert set(db.ALL_TABLES) <= names

    def test_wal_mode(self, temp_db):
        conn = sqlite3.connect(temp_db)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal"


# ===== reviews =====

class TestReviews:
    def test_upsert_and_get(self, temp_db):
        db.upsert_review("tool-calling", "2026-08-31", 2.0, 2.5)
        r = db.get_review("tool-calling")
        assert r["concept"] == "tool-calling"
        assert r["interval_days"] == 2.0
        assert r["ease"] == 2.5
        assert r["times_seen"] == 0

    def test_upsert_updates_existing(self, temp_db):
        """同 concept 再次 upsert 走更新分支，不产生重复行"""
        db.upsert_review("tool-calling", "2026-08-31", 1.0, 2.5)
        db.upsert_review("tool-calling", "2026-09-02", 2.4, 2.6)
        r = db.get_review("tool-calling")
        assert r["interval_days"] == 2.4
        assert r["ease"] == 2.6

    def test_get_due_reviews(self, temp_db):
        db.upsert_review("a", "2026-08-01", 1.0, 2.5)
        db.upsert_review("b", "2026-08-30", 1.0, 2.5)
        db.upsert_review("c", "2026-09-15", 1.0, 2.5)
        due = {r["concept"] for r in db.get_due_reviews("2026-08-30")}
        assert due == {"a", "b"}  # c 未到期

    def test_delete_review(self, temp_db):
        db.upsert_review("x", "2026-08-30", 1.0, 2.5)
        assert db.delete_review("x") is True
        assert db.delete_review("x") is False  # 再删不存在
        assert db.get_review("x") is None


# ===== conversations =====

class TestConversations:
    def test_add_and_list_with_meta(self, temp_db):
        """meta 存 JSON 上下文（M9 选区问答依赖此字段）"""
        meta = {"file": "concepts/tool-calling.md", "selection": "双阶段循环",
                "node_id": "tool-calling"}
        cid = db.add_conversation("user", "什么是双阶段循环？", meta=meta)
        db.add_conversation("assistant", "它是指……")
        rows = db.list_conversations()
        assert len(rows) == 2
        assert rows[0]["id"] == cid or rows[0]["role"] == "assistant"  # 按 id 倒序
        import json
        assert json.loads(rows[-1]["meta"]) == meta  # user 记录带 meta
        assert rows[-2]["meta"] is None  # assistant 无 meta

    def test_list_limit_offset(self, temp_db):
        for i in range(5):
            db.add_conversation("user", f"msg-{i}")
        rows = db.list_conversations(limit=2, offset=1)
        assert len(rows) == 2
        assert rows[0]["content"] == "msg-3"  # 倒序：4,3,2,1 → 跳过1条取2条


# ===== flashcards（一概念多卡）=====

class TestFlashcards:
    def test_multiple_cards_per_concept(self, temp_db):
        """同概念允许两张卡：概念卡 + 诊断式坑卡（M2 评审预留设计）"""
        id1 = db.add_flashcard("chroma-embedding", "bge 模型是什么？", "中文向量模型")
        id2 = db.add_flashcard("chroma-embedding",
                               "症状：检索全是英文乱结果", "根因：默认 MiniLM")
        assert id1 != id2
        cards = db.get_flashcards_by_concept("chroma-embedding")
        assert len(cards) == 2

    def test_get_and_delete(self, temp_db):
        cid = db.add_flashcard("fsrs", "interval 起始值？", "1 天")
        card = db.get_flashcard(cid)
        assert card["front"] == "interval 起始值？"
        assert card["concept"] == "fsrs"
        assert db.delete_flashcard(cid) is True
        assert db.get_flashcard(cid) is None


# ===== sources =====

class TestSources:
    def test_upsert_and_status_change(self, temp_db):
        db.upsert_source("data/raw/test.md", "abc123", 1756500000.0,
                         status="pending")
        s = db.get_source("data/raw/test.md")
        assert s["status"] == "pending"

        db.upsert_source("data/raw/test.md", "abc123", 1756500000.0,
                         wiki_file="summaries/test.md", status="compiled")
        s = db.get_source("data/raw/test.md")
        assert s["status"] == "compiled"
        assert s["wiki_file"] == "summaries/test.md"

    def test_list_by_status(self, temp_db):
        db.upsert_source("a.md", "h1", 1.0, status="pending")
        db.upsert_source("b.md", "h2", 2.0, status="compiled")
        assert [r["path"] for r in db.list_sources(status="pending")] == ["a.md"]
        assert len(db.list_sources()) == 2


# ===== annotations =====

class TestAnnotations:
    def test_add_and_list_ordered_by_offset(self, temp_db):
        """offset 为 Markdown 源字符偏移，列表按 offset 升序"""
        f = "concepts/tool-calling.md"
        db.add_annotation(f, 120, "双阶段", "先非流式拿 tool_calls")
        db.add_annotation(f, 30, "SSE", "流式输出方式")
        rows = db.list_annotations_by_file(f)
        assert [r["offset"] for r in rows] == [30, 120]
        assert rows[0]["note"] == "流式输出方式"

    def test_isolated_by_file(self, temp_db):
        db.add_annotation("a.md", 10, "t", "n1")
        db.add_annotation("b.md", 10, "t", "n2")
        assert len(db.list_annotations_by_file("a.md")) == 1

    def test_get_and_delete(self, temp_db):
        aid = db.add_annotation("a.md", 42, "选中文本", "我的备注")
        a = db.get_annotation(aid)
        assert a["text"] == "选中文本" and a["offset"] == 42
        assert db.delete_annotation(aid) is True
        assert db.get_annotation(aid) is None
