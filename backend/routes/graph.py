"""
图谱数据 API（M7）：GET /api/graph

数据构建：
- 复用 lint 的扫描/解析函数（scan_wiki + related 名字→路径解析），与索引
  文件解耦——即使 _index/_graph 未重建也能返回一致数据（单一数据源）
- 节点：concepts/bugs（frontmatter: title/type/topic）+ topics 页（成员关系）
- 连线：related（名字→路径解析，悬空引用跳过）+ topic → 成员
- 熟练度：reviews 表按 slug 匹配（concept 列 ↔ 节点路径末段），
  interval_days 映射 none/low/mid/high（M11 遗忘地图据此着色）
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from backend.compiler import lint
from backend.compiler.schema import parse_frontmatter
from backend.config import WIKI_DIR, logger
from backend.storage import db

router = APIRouter(prefix="/api/graph", tags=["graph"])


def _mastery_level(interval_days: float | None) -> str:
    """interval → 熟练度档位（无记录=none；数值越小越快忘）"""
    if interval_days is None:
        return "none"
    if interval_days < 3:
        return "low"
    if interval_days < 10:
        return "mid"
    return "high"


@router.get("")
async def get_graph() -> Dict[str, Any]:
    pages = lint.scan_wiki()
    name_map = lint._name_to_path_map(pages)

    reviews = {r["concept"]: r["interval_days"] for r in db.list_reviews()}

    nodes: list[dict] = []
    links: list[dict] = []
    seen_ids: set[str] = set()

    # 概念与坑（related 连线）
    for ptype in ("concept", "bug"):
        for p in pages[ptype]:
            nid = lint._no_ext(p["path"])
            if nid in seen_ids:
                continue
            seen_ids.add(nid)
            meta = p["meta"]
            slug = nid.split("/")[-1]
            nodes.append({
                "id": nid,
                "name": meta.get("title") or slug,
                "type": ptype,
                "topic": meta.get("topic", "") or "",
                "mastery": _mastery_level(reviews.get(slug)),
            })
            rel_paths, _dangling = lint._resolve_related(meta, name_map)
            for target in rel_paths:
                links.append({"source": nid, "target": lint._no_ext(target)})

    # 主题页（topic → 成员连线）
    topics_dir = WIKI_DIR / "topics"
    if topics_dir.exists():
        for f in sorted(topics_dir.glob("*.md")):
            meta, _body = parse_frontmatter(f.read_text(encoding="utf-8"))
            tid = lint._no_ext(f.relative_to(WIKI_DIR).as_posix())
            if tid in seen_ids:
                continue
            seen_ids.add(tid)
            nodes.append({
                "id": tid,
                "name": meta.get("title") or f.stem,
                "type": "topic",
                "topic": "",
                "mastery": "none",
            })
            for member in meta.get("related", []):
                links.append({"source": tid, "target": lint._no_ext(member)})

    # 悬空目标过滤（related 指向不存在的页时解析已跳过；双保险再滤一次）
    valid = set(seen_ids)
    links = [l for l in links if l["source"] in valid and l["target"] in valid]

    stats = {
        "concepts": sum(1 for n in nodes if n["type"] == "concept"),
        "bugs": sum(1 for n in nodes if n["type"] == "bug"),
        "topics": sum(1 for n in nodes if n["type"] == "topic"),
        "links": len(links),
    }
    logger.info(f"图谱数据: {stats}")
    return {"nodes": nodes, "links": links, "stats": stats}
