"""
节点/wiki 内容 API（M8）：
- GET /api/wiki/{path}          页面详情（frontmatter 元信息 + Markdown 正文）
- GET /api/node/{id}/related    正向/反向相关节点 + 所属主题
- GET /api/node/{id}/annotations 该页批注列表（M9 写入，先出读取端点）

约定：annotations.file 与节点 id 一致，存 wiki 相对路径不带 .md
（如 "concepts/fastapi"），与前端路由 id 互相对应。
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.compiler import lint
from backend.config import WIKI_DIR, logger
from backend.storage import db

router = APIRouter(prefix="/api", tags=["wiki"])

_REVIEWS_CACHE: dict | None = None


def _mastery_level(interval_days: float | None) -> str:
    if interval_days is None:
        return "none"
    if interval_days < 3:
        return "low"
    if interval_days < 10:
        return "mid"
    return "high"


def _node_base(nid: str, meta: dict, ptype: str) -> dict:
    """节点基础信息（含熟练度，slug 匹配 reviews）"""
    slug = nid.split("/")[-1]
    interval = next((r["interval_days"] for r in db.list_reviews()
                     if r["concept"] == slug), None)
    return {
        "id": nid,
        "name": meta.get("title") or slug,
        "type": ptype,
        "topic": meta.get("topic", "") or "",
        "mastery": _mastery_level(interval),
        "tags": meta.get("tags", []) or [],
    }


def _load_page(nid: str) -> tuple[dict, str, str]:
    """
    按节点 id（concepts/fastapi）读页面 → (meta, 正文, 相对路径.md)。
    校验路径不逃逸出 wiki 目录。
    """
    page_path = (WIKI_DIR / f"{nid}.md").resolve()
    if not str(page_path).startswith(str(WIKI_DIR.resolve())):
        raise HTTPException(status_code=400, detail="非法路径")
    if not page_path.exists():
        raise HTTPException(status_code=404, detail=f"节点不存在: {nid}")
    text = page_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter_safe(text)
    return meta, body, page_path.relative_to(WIKI_DIR).as_posix()


def parse_frontmatter_safe(text: str):
    from backend.compiler.schema import parse_frontmatter
    return parse_frontmatter(text)


@router.get("/wiki/{path:path}")
async def get_wiki(path: str) -> Dict[str, Any]:
    """页面详情：元信息 + Markdown 正文（不带 .md 也可）"""
    nid = path[:-3] if path.endswith(".md") else path
    meta, body, file_path = _load_page(nid)
    ptype = meta.get("type", "concept")
    base = _node_base(nid, meta, ptype)
    logger.info(f"wiki 页读取: {nid}")
    return {**base, "path": file_path, "content": body,
            "sources": meta.get("sources", []) or [],
            "related_names": meta.get("related", []) or []}


@router.get("/node/{node_id:path}/related")
async def get_related(node_id: str) -> Dict[str, Any]:
    """相关节点：正向（我指向谁）+ 反向（谁指向我）+ 所属主题页"""
    nid = node_id[:-3] if node_id.endswith(".md") else node_id
    meta, _body, _fp = _load_page(nid)
    ptype = meta.get("type", "concept")

    pages = lint.scan_wiki()
    name_map = lint._name_to_path_map(pages)

    # 全量 related 解析（一次扫描构建双向索引）
    outgoing: list[str] = []
    incoming: list[str] = []
    for p in pages["concept"] + pages["bug"]:
        pid = lint._no_ext(p["path"])
        rel_paths, _dangling = lint._resolve_related(p["meta"], name_map)
        rel_ids = [lint._no_ext(t) for t in rel_paths]
        if pid == nid:
            outgoing.extend(rel_ids)
        elif nid in rel_ids and pid not in incoming:
            incoming.append(pid)

    # topics 页成员关系 → 我属于哪些主题
    topic_ids: list[str] = []
    topics_dir = WIKI_DIR / "topics"
    if topics_dir.exists():
        for f in sorted(topics_dir.glob("*.md")):
            tmeta, _b = parse_frontmatter_safe(f.read_text(encoding="utf-8"))
            tid = lint._no_ext(f.relative_to(WIKI_DIR).as_posix())
            members = [lint._no_ext(m) for m in tmeta.get("related", [])]
            if nid in members and tid not in topic_ids:
                topic_ids.append(tid)

    def node_info(xid: str) -> dict | None:
        for ptype2 in ("concept", "bug"):
            for p in pages[ptype2]:
                if lint._no_ext(p["path"]) == xid:
                    return _node_base(xid, p["meta"], ptype2)
        # topic 节点
        tf = WIKI_DIR / f"{xid}.md"
        if tf.exists():
            tmeta, _b2 = parse_frontmatter_safe(tf.read_text(encoding="utf-8"))
            return _node_base(xid, tmeta, "topic")
        return None

    def info_list(ids: list[str]) -> list[dict]:
        out, seen = [], set()
        for xid in ids:
            if xid in seen:
                continue
            seen.add(xid)
            info = node_info(xid)
            if info:
                out.append(info)
        return out

    result = {
        "node": _node_base(nid, meta, ptype),
        "outgoing": info_list(outgoing),
        "incoming": info_list(incoming),
        "topics": info_list(topic_ids),
    }
    logger.info(f"related: {nid} → 出 {len(result['outgoing'])} 入 "
                f"{len(result['incoming'])} 主题 {len(result['topics'])}")
    return result


class AnnotationCreate(BaseModel):
    """批注创建请求（M9 选区加备注）"""
    offset: int
    text: str
    note: str


@router.post("/node/{node_id:path}/annotations")
async def add_annotation(node_id: str, req: AnnotationCreate) -> Dict[str, Any]:
    """
    新增批注。offset 语义（M2 定稿，M9 实现调整为渲染后正文纯文本偏移，
    text 双重定位以 text 匹配为准——渲染 DOM 与 Markdown 源的字符级映射
    成本过高，务实调整，详见实施计划 M9 完成情况）。
    """
    nid = node_id[:-3] if node_id.endswith(".md") else node_id
    # 校验节点存在
    _load_page(nid)
    aid = db.add_annotation(file=nid, offset=req.offset, text=req.text, note=req.note)
    logger.info(f"新增批注: {nid} offset={req.offset}")
    return {"id": aid, "node": nid, "offset": req.offset,
            "text": req.text, "note": req.note}


@router.get("/node/{node_id:path}/annotations")
async def get_annotations(node_id: str) -> Dict[str, Any]:
    """某 wiki 页的批注列表（M9 提供写入）"""
    nid = node_id[:-3] if node_id.endswith(".md") else node_id
    # 兼容 file 存 "concepts/fastapi" 或 "concepts/fastapi.md" 两种历史格式
    rows = db.list_annotations_by_file(nid) or db.list_annotations_by_file(f"{nid}.md")
    return {"node": nid, "annotations": rows}
