"""
RAG ingest（M3）：Markdown 切块 + bge embedding + 写 ChromaDB

关键点（对应已知坑清单）：
- chromadb 1.5.9 的 SentenceTransformerEmbeddingFunction 原生支持
  normalize_embeddings 参数，无需自行包装 EmbeddingFunction（已查证签名）
- 默认 embedding 是英文 MiniLM，collection 创建时必须显式传 bge
- bge-small-zh-v1.5 输出 512 维，归一化后用余弦距离（chromadb 默认 l2 已由
  归一化等价于余弦单调，直接用默认 space 即可）

切块策略（中文友好）：
1. 按 Markdown 标题行（#/##/###）分节，节内标题路径作为上下文前缀
2. 空行分段，段落聚合到不超过 MAX_CHUNK_CHARS
3. 单段超长按 CHUNK_OVERLAP 窗口硬切
4. 尾块过短（< MIN_CHUNK_CHARS）并入前块，避免碎片
5. 每块记录源文件字符偏移 offset（与 annotations 表语义一致）

幂等性：id = "{collection_prefix}::{rel_path}::{index}"，upsert 覆盖；
写入前按 metadata.source 删除该文件旧块，防止切块数变化后残留。
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions

from backend.config import CHROMA_DIR, RAW_DIR, WIKI_DIR, logger

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
RAW_COLLECTION = "raw_chunks"
WIKI_COLLECTION = "wiki_pages"

MAX_CHUNK_CHARS = 500
CHUNK_OVERLAP = 50

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")

_client: chromadb.api.ClientAPI | None = None
_ef: embedding_functions.SentenceTransformerEmbeddingFunction | None = None


def get_client() -> chromadb.api.ClientAPI:
    """PersistentClient 单例（chromadb 内部已处理多 collection 共享）"""
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return _client


def get_ef() -> embedding_functions.SentenceTransformerEmbeddingFunction:
    """bge embedding 函数单例（懒加载，首次调用才加载模型，约数秒）"""
    global _ef
    if _ef is None:
        _ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=MODEL_NAME,
            normalize_embeddings=True,
        )
        logger.info(f"bge embedding 函数已加载: {MODEL_NAME}")
    return _ef


def get_collection(name: str = RAW_COLLECTION):
    """获取（或创建）collection，显式绑定 bge —— 严禁使用默认 MiniLM"""
    return get_client().get_or_create_collection(
        name=name,
        embedding_function=get_ef(),
    )


def chunk_markdown(text: str) -> list[dict]:
    """
    Markdown 中文友好切块。

    Returns:
        [{"text": 块文本（含标题路径前缀）, "title": 标题路径, "offset": 字符偏移}]
    """
    # 第一遍：按标题拆节 → [{"title": 标题栈, "body": 节文本, "offset": 起始偏移}]
    sections: list[dict] = []
    title_path: list[str] = []
    cur_lines: list[str] = []
    cur_title: list[str] = []
    cur_offset = 0
    cursor = 0

    def close_section():
        body = "\n".join(cur_lines).strip()
        if body:
            sections.append({
                "title": list(cur_title),
                "body": body,
                "offset": cur_offset,
            })

    for raw_line in text.splitlines(keepends=True):
        m = _HEADING_RE.match(raw_line.rstrip())
        if m:
            close_section()
            level = len(m.group(1))
            # 新标题栈：截断到上一级，再压入当前标题
            title_path = title_path[: level - 1] + [m.group(2).strip()]
            cur_title = list(title_path)
            cur_lines = []
            cur_offset = cursor
        else:
            if not cur_lines:
                cur_title = list(title_path)
                cur_offset = cursor
        cur_lines.append(raw_line.rstrip("\n"))
        cursor += len(raw_line)
    close_section()

    # 第二遍：节内切块（超长硬切），块文本加标题前缀
    chunks: list[dict] = []
    for sec in sections:
        title_str = "/".join(sec["title"][-2:]) if sec["title"] else ""
        for piece in _split_long(sec["body"]):
            display = f"{title_str}\n{piece}" if title_str else piece
            chunks.append({
                "text": display,
                "title": title_str,
                "offset": sec["offset"],
            })

    # 第三遍：相邻同节过短碎片合并（合并后仍不超过上限）
    merged: list[dict] = []
    for c in chunks:
        if merged and merged[-1]["title"] == c["title"] and \
                len(merged[-1]["text"]) + len(c["text"]) + 1 <= MAX_CHUNK_CHARS:
            base = merged[-1]
            base["text"] += "\n" + c["text"]
        else:
            merged.append(dict(c))
    return merged


def _split_long(body: str, max_len: int = MAX_CHUNK_CHARS,
                overlap: int = CHUNK_OVERLAP) -> list[str]:
    """单段超长按窗口硬切（带 overlap）"""
    if len(body) <= max_len:
        return [body]
    pieces = []
    step = max_len - overlap
    for start in range(0, len(body), step):
        piece = body[start:start + max_len]
        if piece.strip():
            pieces.append(piece)
        if start + max_len >= len(body):
            break
    return pieces


def ingest_file(file_path: Path, collection_name: str = RAW_COLLECTION,
                base_dir: Path | None = None) -> int:
    """
    切块并写入指定 collection（幂等，可重复调用）。

    Args:
        file_path: 源文件路径
        collection_name: RAW_COLLECTION 或 WIKI_COLLECTION
        base_dir: 用于计算相对路径的基准目录（默认按 collection 推断）

    Returns:
        写入的块数
    """
    file_path = Path(file_path)
    if base_dir is None:
        base_dir = RAW_DIR if collection_name == RAW_COLLECTION else WIKI_DIR
    rel_path = file_path.relative_to(base_dir).as_posix()
    text = file_path.read_text(encoding="utf-8")
    mtime = file_path.stat().st_mtime

    # 先删旧块（切块数可能变化），再写入
    delete_source_chunks(rel_path, collection_name)

    pieces = chunk_markdown(text)
    if not pieces:
        logger.warning(f"文件无有效内容，跳过: {rel_path}")
        return 0

    collection = get_collection(collection_name)
    prefix = "raw" if collection_name == RAW_COLLECTION else "wiki"
    collection.upsert(
        ids=[f"{prefix}::{rel_path}::{i}" for i in range(len(pieces))],
        documents=[p["text"] for p in pieces],
        metadatas=[{
            "source": rel_path,
            "chunk_index": i,
            "title": p["title"] or "",
            "offset": p["offset"],
            "mtime": mtime,
        } for i, p in enumerate(pieces)],
    )
    logger.info(f"已入库 {len(pieces)} 块 → {collection_name}: {rel_path}")
    return len(pieces)


def ingest_raw_dir(raw_dir: Path | None = None) -> dict[str, int]:
    """批量收录 raw/ 目录下所有 .md 文件（CLI 入口 / agent ingest_source 使用）"""
    raw_dir = raw_dir or RAW_DIR
    stats: dict[str, int] = {}
    for f in sorted(raw_dir.rglob("*.md")):
        rel = f.relative_to(raw_dir).as_posix()
        stats[rel] = ingest_file(f, RAW_COLLECTION, raw_dir)
    logger.info(f"raw 目录收录完成: {stats}")
    return stats


def delete_source_chunks(rel_path: str, collection_name: str = RAW_COLLECTION) -> None:
    """按 metadata.source 删除某文件的全部旧块（增量更新前置操作）"""
    collection = get_collection(collection_name)
    # 先查再删：collection 对不存在的 where 条目 delete 不报错，但查询可确认存在
    got = collection.get(where={"source": rel_path})
    if got["ids"]:
        collection.delete(where={"source": rel_path})
        logger.info(f"已删除旧块 {len(got['ids'])} 个: {rel_path}")


if __name__ == "__main__":
    # CLI 冒烟：python -m backend.rag.ingest
    stats = ingest_raw_dir()
    print("收录统计:", stats)
