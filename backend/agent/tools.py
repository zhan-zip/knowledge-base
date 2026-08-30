"""
Agent 工具注册表（M5）：定义 + 异步执行器

工具集（实施计划 M5 定稿）：
- search_knowledge(query)      混合检索（BM25 + RAG），回答带来源引用
- get_due_reviews()            获取今天到期的复习卡（含卡面内容）
- generate_flashcard(concept)  为指定概念生成复习卡（LLM 生成 + 落库）
- ingest_source(file_path)     收录文件到 raw/ 并触发编译
- compile_wiki()               手动触发编译管道（增量）

约定：
- 执行器全部 async（compile/generate_flashcard 内部调 LLM）
- 返回字符串（作为 tool 消息回填给 LLM），内容为 JSON 或简短文本
- search_knowledge 截断每条 text，防止 token 爆炸
"""
from __future__ import annotations

import asyncio
import json
import shutil
from datetime import date
from pathlib import Path

from backend.config import RAW_DIR, WIKI_DIR, logger
from backend.compiler.compile import FLASHCARDS_DIR, _write_flashcard, compile_all, compile_source
from backend.compiler.schema import parse_llm_json, slugify
from backend.rag.search import search_knowledge as _search_knowledge
from backend.storage import db

# 检索结果回填 LLM 时的截断限制
MAX_RESULT_TEXT = 400
MAX_RESULTS = 5


# ===== 工具执行器 =====

def _tool_search_knowledge(query: str) -> str:
    """混合检索，返回带来源的结果列表（截断）"""
    out = _search_knowledge(query, top_k=MAX_RESULTS)
    items = []
    for r in out["results"][:MAX_RESULTS]:
        items.append({
            "source": r["source"],
            "kind": r["kind"],
            "title": r["title"],
            "text": r["text"][:MAX_RESULT_TEXT],
        })
    return json.dumps({"route": out["route"], "results": items},
                      ensure_ascii=False, indent=1)


def _tool_get_due_reviews() -> str:
    """今天到期的复习卡（关联卡面内容，供 agent 发起诊断式复习）"""
    today = date.today().isoformat()
    due = db.get_due_reviews(today)
    if not due:
        return "今天没有到期的复习卡片。"
    items = []
    for r in due[:20]:
        cards = db.get_flashcards_by_concept(r["concept"])
        fronts = [c["front"] for c in cards] or ["（无卡面，请按概念名提问）"]
        items.append({"concept": r["concept"], "due": r["due"],
                      "interval_days": r["interval_days"],
                      "card_fronts": fronts})
    return json.dumps({"due_count": len(due), "cards": items},
                      ensure_ascii=False, indent=1)


async def _tool_generate_flashcard(concept: str) -> str:
    """为指定概念（slug 或标题）生成复习卡：读 wiki 页 → LLM 出卡 → 落库"""
    slug = slugify(concept)
    path = WIKI_DIR / "concepts" / f"{slug}.md"
    if not path.exists():
        # slug 未命中则按标题匹配
        found = None
        for f in (WIKI_DIR / "concepts").glob("*.md"):
            meta, _ = _parse_fm(f)
            if meta.get("title") == concept:
                found = f
                break
        if not found:
            return f"未找到概念「{concept}」（wiki/concepts/ 下无对应页面），无法生成复习卡。"
        path = found
        slug = path.stem

    meta, body = _parse_fm(path)
    content = f"概念：{meta.get('title', slug)}\n{body}"[:2000]

    from backend.agent.llm import get_llm_client
    client = get_llm_client()
    resp = await client.chat_completion(
        messages=[
            {"role": "system", "content":
                "你是复习卡生成器。根据概念内容生成一张记忆卡，只输出 JSON："
                '{"front": "提问（触发主动回忆）", "back": "答案（简明扼要）"}。'
                "不要输出其他任何文字。"},
            {"role": "user", "content": content},
        ],
        stream=False, temperature=0.3)
    card = parse_llm_json(resp.choices[0].message.content or "")

    _write_flashcard(concept_slug=slug, title=meta.get("title", slug),
                     related_path=f"concepts/{path.name}",
                     front=str(card.get("front", "")).strip(),
                     back=str(card.get("back", "")).strip())
    return (f"已为概念「{meta.get('title', slug)}」生成复习卡并登记复习计划"
            f"（due={date.today().isoformat()}）。")


async def _tool_ingest_source(file_path: str) -> str:
    """
    收录文件到 raw/ 并触发编译。

    file_path 兼容三种情况：
    - 相对 raw/ 的路径（已在库内）→ 直接编译
    - 项目内其他位置的绝对/相对路径 → 复制进 raw/ 后编译
    - raw/ 下不存在的纯文件名 → 报告不存在
    """
    p = Path(file_path)
    if p.is_absolute() and p.exists():
        target = RAW_DIR / p.name
        if p.resolve() != target.resolve():
            shutil.copy2(p, target)
        rel = p.name
    else:
        rel = p.as_posix()
        candidate = RAW_DIR / rel
        if candidate.exists():
            pass  # 已在 raw/ 内
        elif p.exists() and RAW_DIR not in p.resolve().parents and p.resolve() != target.resolve():
            shutil.copy2(p, target)
            rel = p.name
        else:
            return (f"文件不存在：{file_path}。请确认文件名，或让用户先把文件放到"
                    f"data/raw/ 目录（当前 raw/ 内容：{[f.name for f in RAW_DIR.rglob('*.md')]}）。")

    from backend.rag.ingest import ingest_file
    ingest_file(RAW_DIR / rel)  # 先入向量库（raw_chunks）
    status = await compile_source(RAW_DIR / rel)  # 再增量编译
    return f"文件 {rel} 已收录，编译状态：{status}。"


async def _tool_compile_wiki() -> str:
    """手动触发增量编译管道（compile_all 含 lint 重建与产物入库）"""
    result = await compile_all()
    return ("编译完成：新增编译 {c} 个，跳过 {s} 个，失败 {f} 个。".format(
        c=len(result["compiled"]), s=len(result["skipped"]), f=len(result["failed"])))


def _parse_fm(path: Path):
    from backend.compiler.schema import parse_frontmatter
    return parse_frontmatter(path.read_text(encoding="utf-8"))


# ===== 注册表 =====
TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "search_knowledge",
        "description": "在个人知识库中检索（wiki BM25 优先 + 向量 RAG 兜底）。"
                       "回答知识类问题前应先调用此工具，答案必须附来源引用。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "检索查询词"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "get_due_reviews",
        "description": "获取今天到期的复习卡片。用户想复习/抽查记忆时调用。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "generate_flashcard",
        "description": "为指定概念生成一张复习卡并登记复习计划（编译时已自动生成的概念也可再生成）。",
        "parameters": {"type": "object", "properties": {
            "concept": {"type": "string", "description": "概念名（标题或 slug）"}},
            "required": ["concept"]}}},
    {"type": "function", "function": {
        "name": "ingest_source",
        "description": "把一份 Markdown 资料收录进知识库（复制到 raw/ 并触发编译）。",
        "parameters": {"type": "object", "properties": {
            "file_path": {"type": "string", "description": "文件路径（raw/ 内相对路径或项目内路径）"}},
            "required": ["file_path"]}}},
    {"type": "function", "function": {
        "name": "compile_wiki",
        "description": "手动触发编译管道，把 raw/ 中新增/变更的资料编译进 wiki（增量）。",
        "parameters": {"type": "object", "properties": {}}}},
]


async def execute_tool(name: str, args: dict) -> str:
    """按名称执行工具（未知工具返回错误文本，由 LLM 自行向用户解释）"""
    logger.info(f"执行工具: {name} args={args}")
    try:
        if name == "search_knowledge":
            return _tool_search_knowledge(**args)
        if name == "get_due_reviews":
            return _tool_get_due_reviews()
        if name == "generate_flashcard":
            return await _tool_generate_flashcard(**args)
        if name == "ingest_source":
            return await _tool_ingest_source(**args)
        if name == "compile_wiki":
            return await _tool_compile_wiki()
        return f"未知工具: {name}"
    except Exception as e:
        logger.error(f"工具执行失败 {name}: {e}")
        return f"工具 {name} 执行失败：{type(e).__name__}: {e}"
