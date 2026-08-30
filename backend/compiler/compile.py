"""
编译管道（M4）：raw → LLM 编译 → wiki 五区 + flashcards + 向量库

职责划分：
- compile.py：单份 raw → summaries/concepts/bugs/learning 三区落盘 + 卡片落库
- lint.py：topics 聚合页 + 三索引（_index/_concepts/_graph）重建 + gaps 检测
  （topic 归属存于 concept/bug 的 frontmatter，topic 页由 lint 统一重建，
  避免跨 raw 增量写 topic 页的状态管理）

增量机制：sources 表按 sha1 比对，未变化直接跳过；变化/新增才调 LLM。
JSON 契约：DeepSeek 无严格 JSON mode → system 强调只输出 JSON +
parse_llm_json 容错 + pydantic 校验失败附错误重试一次。
编译完成接线：产物 wiki 页入向量库（wiki_pages）+ BM25 索引重建 + lint 全量重建。

CLI 冒烟：python -m backend.compiler.compile
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date
from pathlib import Path

from backend.config import LEARNING_DIR, RAW_DIR, WIKI_DIR, logger
from backend.compiler import lint
from backend.compiler.schema import (
    SummaryOutput,
    ValidationError,
    new_page_meta,
    parse_frontmatter,
    slugify,
    to_frontmatter,
    validate_summary_output,
)
from backend.agent.llm import get_llm_client
from backend.rag.ingest import WIKI_COLLECTION, ingest_file
from backend.rag.search import bm25_index
from backend.storage import db

FLASHCARDS_DIR = LEARNING_DIR / "flashcards"

_SYSTEM_PROMPT = (
    "你是个人知识库的编译器。你的任务是把用户的原始笔记编译为结构化 JSON。"
    "只输出 JSON 本身，不要输出任何解释文字、不要使用代码围栏。"
)

_USER_TMPL = """请把下面这份原始资料编译为 JSON，字段结构如下：

{{
  "summary": "全文摘要，200 字内 Markdown",
  "topics": ["涉及的主题名"],
  "concepts": [
    {{
      "name": "概念名（简短）",
      "topic": "所属主题名",
      "definition": "一句话定义",
      "details": "要点展开，Markdown 列表",
      "related": ["同文档内相关概念名或坑名"],
      "flashcard": {{"front": "记忆提问", "back": "答案"}}
    }}
  ],
  "bugs": [
    {{
      "name": "坑名（症状式短语，如：ChromaDB 连接超时）",
      "topic": "所属主题名",
      "related_concepts": ["关联概念名"],
      "symptoms": "症状：现象与报错信息",
      "root_cause": "根因",
      "solution": "解法",
      "reproduction": "复现步骤，无则空字符串",
      "prevention": "预防措施",
      "diagnosis_steps": ["排查步骤（按判断顺序）"],
      "flashcard": {{"front": "症状描述（诊断题面）", "back": "根因 + 解法"}}
    }}
  ]
}}

要求：
1. 只提取与做 agent 项目相关的实质内容，宁缺毋滥；纯流水账不提名
2. 坑是一等公民：原文出现报错/踩坑/修复过程，必须提名为 bug，并给出排查步骤
3. 不得编造原文没有的内容；related 只能引用本文档内出现的名字

原始资料：
---
{raw_text}
---"""


def _sha1_of(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def _prompt_with_error(raw_text: str, last_output: str, error: str) -> str:
    """校验失败后的重试 prompt：附错误与上次输出要求修正"""
    return (
        f"{_USER_TMPL.format(raw_text=raw_text)}\n\n"
        f"你上一次的输出存在以下问题：\n{error}\n\n"
        f"上次输出（截断）：\n{last_output[:2000]}\n\n"
        "请修正后重新输出完整 JSON，仍然只输出 JSON。"
    )


async def _call_llm_json(raw_text: str) -> SummaryOutput:
    """LLM 编译单份 raw → SummaryOutput，校验失败重试一次（设计规定）"""
    client = get_llm_client()
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _USER_TMPL.format(raw_text=raw_text)},
    ]
    last_text, last_err = "", ""
    for attempt in (1, 2):
        resp = await client.chat_completion(
            messages=messages, stream=False, temperature=0.2)
        last_text = resp.choices[0].message.content or ""
        try:
            return validate_summary_output(last_text)
        except (ValidationError, ValueError, json.JSONDecodeError) as e:
            last_err = str(e)[:500]
            logger.warning(f"LLM 输出校验失败（第 {attempt} 次）：{last_err}；"
                           f"原始输出前200字：{last_text[:200]!r}")
            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _prompt_with_error(
                    raw_text, last_text, last_err)},
            ]
    raise ValueError(f"LLM 输出两次校验均失败：{last_err}")


# ===== 落盘函数 =====

def _write_summary(raw_rel: str, out: SummaryOutput) -> Path:
    slug = slugify(Path(raw_rel).stem)
    path = WIKI_DIR / "summaries" / f"{slug}.md"
    meta = new_page_meta(title=f"摘要：{Path(raw_rel).stem}", page_type="summary",
                         sources=[raw_rel], tags=out.topics)
    body = (f"{out.summary}\n\n"
            f"## 提名清单\n\n"
            f"- 概念：{', '.join(c.name for c in out.concepts) or '无'}\n"
            f"- 坑：{', '.join(b.name for b in out.bugs) or '无'}\n")
    path.write_text(to_frontmatter(meta, body), encoding="utf-8")
    return path


def _merge_concept(nom, raw_rel: str) -> tuple[Path, bool]:
    """概念页不存在则创建（含复习卡），存在则追加来源与补充段。返回 (路径, 是否新建)"""
    slug = slugify(nom.name)
    path = WIKI_DIR / "concepts" / f"{slug}.md"
    created = not path.exists()

    if created:
        meta = new_page_meta(title=nom.name, page_type="concept",
                             sources=[raw_rel], topic=nom.topic,
                             related=nom.related)
        body = (f"{nom.definition}\n\n"
                f"## 要点\n\n{nom.details}\n\n"
                f"## 来源\n\n- {raw_rel}\n")
    else:
        meta, old_body = parse_frontmatter(
            path.read_text(encoding="utf-8"))
        for s in [raw_rel]:
            if s not in meta.get("sources", []):
                meta["sources"] = meta.get("sources", []) + [s]
        for r in nom.related:
            if r not in meta.get("related", []):
                meta["related"] = meta.get("related", []) + [r]
        if nom.topic and not meta.get("topic"):
            meta["topic"] = nom.topic
        body = (f"{old_body}\n\n"
                f"## 来自 {raw_rel} 的补充\n\n{nom.details}\n")

    path.write_text(to_frontmatter(meta, body), encoding="utf-8")

    if created:
        _write_flashcard(concept_slug=slug, title=nom.name,
                         related_path=f"concepts/{slug}.md",
                         front=nom.flashcard.front, back=nom.flashcard.back)
    return path, created


def _merge_bug(nom, raw_rel: str) -> tuple[Path, bool]:
    slug = slugify(nom.name)
    path = WIKI_DIR / "bugs" / f"{slug}.md"
    created = not path.exists()

    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(nom.diagnosis_steps, 1))
    if created:
        meta = new_page_meta(title=nom.name, page_type="bug",
                             sources=[raw_rel], topic=nom.topic,
                             related=nom.related_concepts)
        body = (f"## 症状\n\n{nom.symptoms}\n\n"
                f"## 根因\n\n{nom.root_cause}\n\n"
                f"## 解法\n\n{nom.solution}\n\n")
        if nom.reproduction:
            body += f"## 复现\n\n{nom.reproduction}\n\n"
        body += f"## 预防\n\n{nom.prevention}\n"
        if steps:
            body += f"\n## 排查决策步骤\n\n{steps}\n"
        body += f"\n## 来源\n\n- {raw_rel}\n"
    else:
        meta, old_body = parse_frontmatter(path.read_text(encoding="utf-8"))
        if raw_rel not in meta.get("sources", []):
            meta["sources"] = meta.get("sources", []) + [raw_rel]
        for r in nom.related_concepts:
            if r not in meta.get("related", []):
                meta["related"] = meta.get("related", []) + [r]
        if nom.topic and not meta.get("topic"):
            meta["topic"] = nom.topic
        body = (f"{old_body}\n\n"
                f"## 来自 {raw_rel} 的补充\n\n{nom.root_cause}\n\n{nom.solution}\n")

    path.write_text(to_frontmatter(meta, body), encoding="utf-8")

    if created:
        _write_flashcard(concept_slug=slug, title=nom.name,
                         related_path=f"bugs/{slug}.md",
                         front=nom.flashcard.front, back=nom.flashcard.back)
    return path, created


def _write_flashcard(concept_slug: str, title: str, related_path: str,
                     front: str, back: str) -> None:
    """卡片 md 镜像 + db 落卡 + reviews 首次登记（due=今天，interval=1，ease=2.5）"""
    FLASHCARDS_DIR.mkdir(parents=True, exist_ok=True)
    path = FLASHCARDS_DIR / f"{concept_slug}.md"
    meta = new_page_meta(title=f"卡：{title}", page_type="concept",
                         sources=[], related=[related_path])
    path.write_text(
        to_frontmatter(meta, f"**正面**\n\n{front}\n\n**背面**\n\n{back}\n"),
        encoding="utf-8")
    db.add_flashcard(concept=concept_slug, front=front, back=back)
    db.upsert_review(concept=concept_slug, due=date.today().isoformat(),
                     interval_days=1.0, ease=2.5)
    logger.info(f"复习卡已生成并登记: {concept_slug}")


# ===== 主流程 =====

async def compile_source(file_path: Path) -> str:
    """
    编译单份 raw 文件（增量：sha1 未变则跳过）。

    Returns:
        "compiled" / "skipped" / "failed"
    """
    file_path = Path(file_path)
    rel = file_path.relative_to(RAW_DIR).as_posix()
    sha1, mtime = _sha1_of(file_path), file_path.stat().st_mtime

    prev = db.get_source(rel)
    if prev and prev["sha1"] == sha1 and prev["status"] == "compiled":
        logger.info(f"跳过未变化文件: {rel}")
        return "skipped"

    raw_text = file_path.read_text(encoding="utf-8")
    try:
        out = await _call_llm_json(raw_text)
    except Exception as e:
        logger.error(f"编译失败 {rel}: {e}")
        db.upsert_source(rel, sha1, mtime, status="failed")
        return "failed"

    summary_path = _write_summary(rel, out)
    for nom in out.concepts:
        _merge_concept(nom, rel)
    for nom in out.bugs:
        _merge_bug(nom, rel)

    db.upsert_source(rel, sha1, mtime,
                     wiki_file=summary_path.relative_to(WIKI_DIR).as_posix(),
                     status="compiled")
    logger.info(f"编译完成: {rel} → {len(out.concepts)} 概念, {len(out.bugs)} 坑")
    return "compiled"


def _ingest_wiki_pages() -> int:
    """全部 wiki 页 + 复习卡镜像入向量库（wiki_pages），并重建 BM25 索引"""
    count = 0
    targets = []
    for sub in ("summaries", "concepts", "bugs"):
        targets += sorted((WIKI_DIR / sub).rglob("*.md"))
    for f in targets:
        count += ingest_file(f, WIKI_COLLECTION, WIKI_DIR)
    bm25_index.build()
    logger.info(f"wiki 向量入库 {count} 块，BM25 已重建")
    return count


async def compile_all(raw_dir: Path | None = None) -> dict:
    """增量编译 raw/ 全部文件 + lint 重建 + 产物入库。CLI 与 agent 工具共用入口"""
    raw_dir = raw_dir or RAW_DIR
    result = {"compiled": [], "skipped": [], "failed": []}
    for f in sorted(raw_dir.rglob("*.md")):
        status = await compile_source(f)
        result[status].append(f.relative_to(raw_dir).as_posix())

    lint.rebuild_indexes()          # topics 聚合页 + 三索引
    _ingest_wiki_pages()            # 产物入向量库 + BM25 重建
    gaps = lint.detect_gaps()
    if gaps:
        logger.warning(f"gaps 检测 {len(gaps)} 项: {gaps}")
    logger.info(f"编译汇总: {result}")
    return result


if __name__ == "__main__":
    # CLI 冒烟：python -m backend.compiler.compile
    print(asyncio.run(compile_all()))
