"""
混合检索（M3）：wiki BM25 优先 → ChromaDB 向量兜底（路由顺序可控）

- BM25：rank_bm25 对 wiki 目录全部 .md 建文档级索引（标题加权：标题词重复计入），
  分词零依赖（中文按单字 + 英文/数字按整词）；服务启动时 build()，编译后 rebuild
- 向量：chromadb 双 collection（raw_chunks / wiki_pages），bge 检索；
  bge 官方模型卡建议 query 侧加指令前缀（passage 侧不加），已实现
- 路由：BM25 top1 ≥ 阈值 → 直接返回 wiki 命中；否则向量检索两个 collection
  按 distance 升序合并，并把弱 BM25 命中降权并入，保证答案可附来源

CLI 冒烟：python -m backend.rag.search "查询词"
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from rank_bm25 import BM25Okapi

from backend.config import WIKI_DIR, logger
from backend.rag.ingest import RAW_COLLECTION, WIKI_COLLECTION, get_collection

# BM25 命中阈值（0~∞，无上限；2.5 约对应命中 2~3 个查询词，可按实际效果调）
BM25_THRESHOLD = 2.5
# bge 官方建议：短 query 检索时给 query 加指令前缀（仅 query 侧）
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """零依赖分词：英文/数字整词 + 中文单字（BM25 字粒度对中文可用）"""
    return _TOKEN_RE.findall(text.lower())


class BM25WikiIndex:
    """wiki 文档级 BM25 索引（进程内单例，编译后 rebuild）"""

    def __init__(self):
        self.docs: list[dict] = []   # [{"path", "title", "text"}]
        self._bm25: BM25Okapi | None = None

    def build(self, wiki_dir: Path | None = None) -> int:
        """扫描 wiki 目录建索引，返回文档数；目录为空时索引置空"""
        wiki_dir = wiki_dir or WIKI_DIR
        self.docs = []
        if wiki_dir.exists():
            for f in sorted(wiki_dir.rglob("*.md")):
                try:
                    text = f.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    logger.warning(f"跳过非 UTF-8 文件: {f}")
                    continue
                first_line = next(
                    (ln.lstrip("# ").strip() for ln in text.splitlines() if ln.strip()),
                    f.stem,
                )
                self.docs.append({
                    "path": f.relative_to(wiki_dir).as_posix(),
                    "title": first_line or f.stem,
                    "text": text,
                })
        if self.docs:
            corpus = []
            for d in self.docs:
                # 标题加权：标题 token 计 3 次
                title_tokens = tokenize(d["title"]) * 3
                corpus.append(title_tokens + tokenize(d["text"]))
            self._bm25 = BM25Okapi(corpus)
        else:
            self._bm25 = None
        logger.info(f"BM25 索引构建完成: {len(self.docs)} 篇 wiki 文档")
        return len(self.docs)

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """返回 [{"path", "title", "text", "score"}]，score 降序"""
        if self._bm25 is None or not self.docs:
            return []
        scores = self._bm25.get_scores(tokenize(query))
        ranked = sorted(zip(range(len(self.docs)), scores),
                        key=lambda x: x[1], reverse=True)[:top_k]
        hits = []
        for idx, score in ranked:
            if score <= 0:
                break  # 0 分及以下无意义，提前截断
            d = self.docs[idx]
            hits.append({**d, "score": float(score)})
        return hits


# 进程级单例（FastAPI 启动时 build；编译管道完成后调用 rebuild）
bm25_index = BM25WikiIndex()


def search_vectors(query: str, top_k: int = 5) -> list[dict]:
    """bge 向量检索 raw_chunks + wiki_pages 两个 collection，合并按 distance 升序"""
    q = QUERY_INSTRUCTION + query
    results: list[dict] = []
    for coll_name, kind in ((RAW_COLLECTION, "raw"), (WIKI_COLLECTION, "wiki")):
        collection = get_collection(coll_name)
        got = collection.query(query_texts=[q], n_results=top_k,
                               include=["documents", "metadatas", "distances"])
        docs = got.get("documents") or [[]]
        metas = got.get("metadatas") or [[]]
        dists = got.get("distances") or [[]]
        for doc, meta, dist in zip(docs[0], metas[0], dists[0]):
            results.append({
                "kind": kind,
                "source": meta.get("source", ""),
                "title": meta.get("title", ""),
                "offset": meta.get("offset", 0),
                "text": doc,
                # 归一化 embedding 下 l2 距离单调等价余弦距离，越小越相关
                "distance": float(dist),
            })
    results.sort(key=lambda r: r["distance"])
    return results[:top_k]


def search_knowledge(query: str, top_k: int = 5) -> dict:
    """
    混合检索主入口（agent 工具 search_knowledge 直接调用）。

    路由策略：
    1. BM25 检索 wiki，top1 ≥ BM25_THRESHOLD → route=wiki_bm25，直接返回
    2. 否则向量检索双 collection，弱 BM25 命中按 score 折算后合并去重
    """
    bm25_hits = bm25_index.search(query, top_k)
    if bm25_hits and bm25_hits[0]["score"] >= BM25_THRESHOLD:
        logger.info(f"检索路由: BM25 命中 (top1={bm25_hits[0]['score']:.2f})")
        return {
            "route": "wiki_bm25",
            "results": [{
                "kind": "wiki",
                "source": h["path"],
                "title": h["title"],
                "offset": 0,
                "text": h["text"],
                "score": round(h["score"], 3),
            } for h in bm25_hits],
        }

    vec_hits = search_vectors(query, top_k)
    merged: list[dict] = [{
        "kind": h["kind"],
        "source": h["source"],
        "title": h["title"],
        "offset": h["offset"],
        "text": h["text"],
        "score": round(1.0 / (1.0 + h["distance"]), 3),  # distance → 相似度分
    } for h in vec_hits]

    # 弱 BM25 命中降权并入（score × 0.5），便于答案附 wiki 来源
    seen = {(r["kind"], r["source"]) for r in merged}
    for h in bm25_hits:
        key = ("wiki", h["path"])
        if key not in seen:
            merged.append({
                "kind": "wiki",
                "source": h["path"],
                "title": h["title"],
                "offset": 0,
                "text": h["text"],
                "score": round(h["score"] * 0.5, 3),
            })

    merged.sort(key=lambda r: r["score"], reverse=True)
    logger.info(f"检索路由: 向量兜底 (bm25_top1={bm25_hits[0]['score'] if bm25_hits else 0:.2f}, "
                f"向量 {len(vec_hits)} 条)")
    return {"route": "vector_fallback", "results": merged[:top_k]}


if __name__ == "__main__":
    # CLI 冒烟：python -m backend.rag.search "查询词"
    q = sys.argv[1] if len(sys.argv) > 1 else "ChromaDB 连接超时怎么解决"
    bm25_index.build()
    out = search_knowledge(q)
    print(f"路由: {out['route']}")
    for i, r in enumerate(out["results"], 1):
        print(f"\n[{i}] {r['kind']} | {r['source']} | score={r['score']}")
        print(r["text"][:200].replace("\n", " "))
