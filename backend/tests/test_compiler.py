"""
M4 编译管道测试

运行方式（项目根目录）：
    python -m pytest backend/tests/ -v

覆盖：schema（slugify/frontmatter/LLM JSON 契约）+ lint（索引重建/gaps）
     + 合并落盘（不调 LLM，构造提名对象直接测）
真实 LLM 端到端编译用 CLI 冒烟（见实施计划测试记录），不进 pytest
（避免单测依赖外部服务与消耗 token）
"""
import json
from pathlib import Path

import pytest

import backend.compiler.lint as lint
import backend.storage.db as db
from backend.compiler.compile import _merge_bug, _merge_concept, _sha1_of, _write_summary
from backend.compiler.schema import (
    BugNomination,
    ConceptNomination,
    FlashcardOut,
    new_page_meta,
    parse_frontmatter,
    slugify,
    to_frontmatter,
    validate_summary_output,
)


# ===== fixture =====

@pytest.fixture()
def wiki_env(tmp_path, monkeypatch):
    """临时 wiki 目录（四区齐全），patch lint.WIKI_DIR"""
    wiki = tmp_path / "wiki"
    for sub in ("summaries", "concepts", "bugs", "topics"):
        (wiki / sub).mkdir(parents=True)
    monkeypatch.setattr(lint, "WIKI_DIR", wiki)
    return wiki


@pytest.fixture()
def compile_env(tmp_path, monkeypatch, wiki_env):
    """compile 模块环境：wiki + learning + db 全临时"""
    import backend.compiler.compile as compile_mod
    learning = tmp_path / "learning"
    (learning / "flashcards").mkdir(parents=True)
    monkeypatch.setattr(compile_mod, "WIKI_DIR", wiki_env)
    monkeypatch.setattr(compile_mod, "LEARNING_DIR", learning)
    monkeypatch.setattr(compile_mod, "FLASHCARDS_DIR", learning / "flashcards")
    db_path = tmp_path / "kb.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    db.init_db()
    return {"wiki": wiki_env, "raw_rel": "raw/test.md"}


def _make_concept(name, topic="测试主题", related=None, details="要点内容。"):
    return ConceptNomination(
        name=name, topic=topic, definition=f"{name} 的定义。",
        details=details, related=related or [],
        flashcard=FlashcardOut(front=f"什么是{name}？", back=f"{name} 的答案"))


def _make_bug(name, topic="测试主题", related=None):
    return BugNomination(
        name=name, topic=topic, related_concepts=related or [],
        symptoms="报错超时", root_cause="权限不足", solution="改权限",
        reproduction="步骤一", prevention="初始化校验",
        diagnosis_steps=["看报错", "查权限"],
        flashcard=FlashcardOut(front="症状：超时报错", back="根因：权限不足"))


# ===== schema =====

class TestSlugify:
    def test_chinese_kept(self):
        assert slugify("双阶段 循环") == "双阶段-循环"

    def test_english_lower_kebab(self):
        assert slugify("Tool Calling Loop") == "tool-calling-loop"

    def test_illegal_chars(self):
        assert "/" not in slugify("a/b\\c:d")
        assert slugify("FSRS（间隔复习）") .startswith("fsrs")

    def test_empty_fallback(self):
        assert slugify("  /// ") == "untitled"


class TestFrontmatter:
    def test_round_trip(self):
        meta = new_page_meta("双阶段循环", "concept", ["raw/a.md"],
                             topic="Agent", related=["坑A"])
        text = to_frontmatter(meta, "正文内容")
        fm, body = parse_frontmatter(text)
        assert fm["title"] == "双阶段循环"
        assert fm["type"] == "concept"
        assert fm["sources"] == ["raw/a.md"]
        assert fm["related"] == ["坑A"]
        assert "正文内容" in body

    def test_no_frontmatter(self):
        fm, body = parse_frontmatter("没有 frontmatter 的普通文本")
        assert fm == {}
        assert "普通文本" in body

    def test_broken_yaml_returns_original(self):
        fm, body = parse_frontmatter("---\n{: broken\n---\n正文")
        assert "正文" in body  # 不抛异常，退化处理


class TestLLMJsonContract:
    def test_parse_with_fence(self):
        text = '```json\n{"a": 1}\n```'
        assert json.__name__ and validate_summary_output.__name__  # 占位引模块
        from backend.compiler.schema import parse_llm_json
        assert parse_llm_json(text) == {"a": 1}

    def test_parse_with_noise(self):
        from backend.compiler.schema import parse_llm_json
        assert parse_llm_json('前缀说明 {"a": 1} 后缀说明') == {"a": 1}

    def test_validate_ok(self):
        out = {
            "summary": "摘要", "topics": ["T"],
            "concepts": [{"name": "C", "topic": "T", "definition": "定义",
                          "details": "细节", "related": [],
                          "flashcard": {"front": "F", "back": "B"}}],
            "bugs": [],
        }
        model = validate_summary_output(json.dumps(out, ensure_ascii=False))
        assert model.concepts[0].name == "C"

    def test_validate_missing_field_raises(self):
        """summary 是唯一必填字段（topics/concepts/bugs 均有默认值）"""
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            validate_summary_output('{"topics": ["缺 summary"]}')

    def test_validate_minimal_ok(self):
        model = validate_summary_output('{"summary": "只有摘要"}')
        assert model.concepts == [] and model.bugs == []


# ===== 合并落盘（不调 LLM）=====

class TestMergePages:
    def test_merge_concept_create_then_append(self, compile_env):
        """首次创建 + 二次来源追加（不重复建卡）"""
        nom = _make_concept("双阶段循环")
        path, created = _merge_concept(nom, "raw/a.md")
        assert created and path.exists()
        n_cards = db.list_flashcards()
        assert len(n_cards) == 1 and db.get_review("双阶段循环")

        nom2 = _make_concept("双阶段循环", details="新补充要点。")
        path2, created2 = _merge_concept(nom2, "raw/b.md")
        assert not created2
        text = path2.read_text(encoding="utf-8")
        assert "raw/a.md" in text and "raw/b.md" in text
        assert "来自 raw/b.md 的补充" in text
        assert len(db.list_flashcards()) == 1  # 卡不重复

    def test_merge_bug_structure(self, compile_env):
        path, created = _merge_bug(_make_bug("ChromaDB 超时"), "raw/a.md")
        assert created
        text = path.read_text(encoding="utf-8")
        for section in ("## 症状", "## 根因", "## 解法", "## 预防", "## 排查决策步骤"):
            assert section in text

    def test_write_summary(self, compile_env):
        from backend.compiler.schema import SummaryOutput
        out = SummaryOutput(summary="这是摘要。", topics=["T"],
                            concepts=[_make_concept("C1")],
                            bugs=[_make_bug("B1")])
        path = _write_summary("raw/test.md", out)
        assert path.exists() and path.name == "test.md"
        fm, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        assert fm["type"] == "summary" and fm["sources"] == ["raw/test.md"]

    def test_sha1_detects_change(self, tmp_path):
        f = tmp_path / "x.md"
        f.write_text("v1", encoding="utf-8")
        s1 = _sha1_of(f)
        f.write_text("v2", encoding="utf-8")
        assert _sha1_of(f) != s1


# ===== lint：索引与 gaps =====

class TestLint:
    def _seed(self, wiki: Path):
        """两个概念 + 一个坑：A→B 引用，C 孤立，坑引用悬空名"""
        def put(sub, slug, title, ptype, related, topic, body):
            meta = new_page_meta(title, ptype, ["raw/a.md"], topic=topic,
                                 related=related)
            (wiki / sub / f"{slug}.md").write_text(
                to_frontmatter(meta, body), encoding="utf-8")

        put("concepts", "con-a", "概念A", "concept", ["概念B"], "主题一",
            "概念A 的定义。\n要点。")
        put("concepts", "con-b", "概念B", "concept", [], "主题一",
            "概念B 的定义。")
        put("concepts", "con-c", "概念C", "concept", [], "主题二",
            "概念C 的定义。")
        put("bugs", "bug-x", "坑X", "bug", ["不存在的名"], "主题一",
            "## 症状\n报错\n\n## 根因\n权限\n")

    def test_rebuild_topics_and_graph(self, wiki_env):
        self._seed(wiki_env)
        stats = lint.rebuild_indexes()
        assert stats["topics"] == 2  # 主题一、主题二
        # topics 页成员正确
        t1 = (wiki_env / "topics" / "主题一.md").read_text(encoding="utf-8")
        assert "概念A" in t1 and "坑X" in t1 and "概念C" not in t1
        # _graph.md：related 名字→路径解析
        graph = (wiki_env / "_graph.md").read_text(encoding="utf-8")
        assert "concepts/con-a|concept|主题一|concepts/con-b" in graph
        assert "bugs/bug-x|bug|主题一|" in graph
        assert "topics/主题一|topic" in graph
        # 三索引文件齐全
        assert (wiki_env / "_index.md").exists()
        assert (wiki_env / "_concepts.md").exists()

    def test_detect_gaps(self, wiki_env):
        self._seed(wiki_env)
        lint.rebuild_indexes()  # 生成 topics 页供引用分析
        gaps = "\n".join(lint.detect_gaps())
        assert "孤立节点" in gaps and "concepts/con-c" in gaps
        assert "悬空引用" in gaps and "不存在的名" in gaps

    def test_stale_topic_page_removed(self, wiki_env):
        """topic 成员清空后旧 topic 页被清理"""
        self._seed(wiki_env)
        stale = wiki_env / "topics" / "旧主题.md"
        stale.write_text("陈旧内容", encoding="utf-8")
        lint.rebuild_indexes()
        assert not stale.exists()
