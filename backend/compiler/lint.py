"""
wiki lint（M4）：topics 聚合页 + 三索引重建 + gaps 检测

- topics 页不增量维护：concept/bug frontmatter 带 topic 字段，这里全量重建
  （避免跨 raw 增量写 topic 页的状态管理）
- _index.md：summaries/concepts/bugs/topics 四区清单
- _concepts.md：概念索引（title → 文件 → 来源）
- _graph.md：邻接表（管道分隔行，M7 图谱数据源）：
    页面路径|类型|topic|related路径1,related路径2
  frontmatter.related 存的是名字，这里经 name→path 映射转换为路径；
  无法映射的名字记入 gaps（悬空引用）
- detect_gaps：孤立节点 / 悬空引用 / bug 缺根因 / 概念缺定义

CLI：python -m backend.compiler.lint
"""
from __future__ import annotations

from pathlib import Path

from backend.config import WIKI_DIR, logger
from backend.compiler.schema import parse_frontmatter, slugify


def _index_file() -> Path:
    """索引文件路径按当前 WIKI_DIR 派生（函数内取值，测试 patch 生效；
    勿用模块级常量——导入时求值会绕过 monkeypatch 并污染真实目录）"""
    return WIKI_DIR / "_index.md"


def _concepts_index() -> Path:
    return WIKI_DIR / "_concepts.md"


def _graph_file() -> Path:
    return WIKI_DIR / "_graph.md"


def scan_wiki() -> dict[str, list[dict]]:
    """
    扫描 wiki 三区（summaries/concepts/bugs），解析 frontmatter。

    Returns:
        {"summary": [...], "concept": [...], "bug": [...]}
        每项：{"path": 相对 wiki 路径, "meta": frontmatter, "body": 正文,
               "definition": 正文首个非标题行（concept/bug 用于展示）}
    """
    result: dict[str, list[dict]] = {"summary": [], "concept": [], "bug": []}
    type_dir = {"summary": "summaries", "concept": "concepts", "bug": "bugs"}
    for ptype, sub in type_dir.items():
        for f in sorted((WIKI_DIR / sub).rglob("*.md")):
            meta, body = parse_frontmatter(f.read_text(encoding="utf-8"))
            definition = next(
                (ln.strip() for ln in body.splitlines()
                 if ln.strip() and not ln.strip().startswith("#")),
                "")
            result[ptype].append({
                "path": f.relative_to(WIKI_DIR).as_posix(),
                "meta": meta,
                "body": body,
                "definition": definition,
            })
    return result


def _name_to_path_map(pages: dict[str, list[dict]]) -> dict[str, str]:
    """title → 页面路径 映射（related 名字解析用；同名单取概念优先于坑）"""
    m: dict[str, str] = {}
    for ptype in ("concept", "bug"):  # 后写不覆盖：concept 先建
        for p in pages[ptype]:
            title = p["meta"].get("title", "")
            m.setdefault(title, p["path"])
    return m


def rebuild_topics(pages: dict[str, list[dict]]) -> list[Path]:
    """按 concept/bug 的 frontmatter.topic 分组重建 topics/*.md，返回写入的文件"""
    groups: dict[str, list[tuple[str, dict]]] = {}
    for ptype in ("concept", "bug"):
        for p in pages[ptype]:
            topic = (p["meta"].get("topic") or "").strip()
            if topic:
                groups.setdefault(topic, []).append((ptype, p))

    topics_dir = WIKI_DIR / "topics"
    topics_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for topic, members in sorted(groups.items()):
        slug = slugify(topic)
        path = topics_dir / f"{slug}.md"
        related = [p["path"] for _, p in members]
        lines = [f"# {topic}\n"]
        for ptype, p in sorted(members, key=lambda x: x[1]["path"]):
            label = "概念" if ptype == "concept" else "坑"
            d = p["definition"]
            suffix = f" —— {d[:60]}" if d else ""
            lines.append(f"- **[{label}]** [{p['meta'].get('title', p['path'])}]"
                         f"(../{p['path']}){suffix}")
        from datetime import date
        meta = {"title": topic, "type": "topic", "topic": "",
                "sources": [], "related": related, "tags": [],
                "created": date.today().isoformat()}
        from backend.compiler.schema import to_frontmatter
        path.write_text(to_frontmatter(meta, "\n".join(lines)), encoding="utf-8")
        written.append(path)

    # 清理已无成员的旧 topic 页
    topic_slugs = {slugify(t) for t in groups}
    for old in topics_dir.glob("*.md"):
        if old.stem not in topic_slugs:
            old.unlink()
            logger.info(f"移除空 topic 页: {old.name}")
    return written


def _resolve_related(meta: dict, name_map: dict[str, str]) -> tuple[list[str], list[str]]:
    """frontmatter.related 名字 → 路径列表；返回 (路径列表, 悬空名字列表)"""
    resolved, dangling = [], []
    for name in meta.get("related", []):
        if name in name_map:
            resolved.append(name_map[name])
        else:
            dangling.append(name)
    return resolved, dangling


def _no_ext(path: str) -> str:
    """图谱节点路径不带 .md 后缀（节点 id 惯例，M7 直接使用）"""
    return path[:-3] if path.endswith(".md") else path


def rebuild_indexes() -> dict:
    """全量重建 topics 页 + _index.md + _concepts.md + _graph.md"""
    pages = scan_wiki()
    name_map = _name_to_path_map(pages)
    written_topics = rebuild_topics(pages)

    # _graph.md：邻接表（含 topics 页成员关系）
    graph_lines = ["# 图谱邻接表（lint 自动生成，勿手改）", ""]
    dangling_all: list[str] = []
    for ptype in ("concept", "bug"):
        for p in pages[ptype]:
            rel_paths, dangling = _resolve_related(p["meta"], name_map)
            dangling_all += [f"{p['path']} → {d}" for d in dangling]
            topic = p["meta"].get("topic", "") or ""
            graph_lines.append(
                f"{_no_ext(p['path'])}|{ptype}|{topic}|"
                f"{','.join(_no_ext(r) for r in rel_paths)}")
    # topic 页 → 成员（方向：topic 指向成员）
    topics_dir = WIKI_DIR / "topics"
    for f in sorted(topics_dir.glob("*.md")):
        meta, _ = parse_frontmatter(f.read_text(encoding="utf-8"))
        rel = _no_ext(f.relative_to(WIKI_DIR).as_posix())
        graph_lines.append(
            f"{rel}|topic|{meta.get('title', '')}|"
            f"{','.join(_no_ext(r) for r in meta.get('related', []))}")
    _graph_file().write_text("\n".join(graph_lines) + "\n", encoding="utf-8")

    # _index.md：四区清单
    idx = ["# 知识库总索引（lint 自动生成，勿手改）", ""]
    sections = [("summaries", "摘要", pages["summary"]),
                ("concepts", "概念", pages["concept"]),
                ("bugs", "坑", pages["bug"]),
                ("topics", "主题", None)]
    for sub, label, plist in sections:
        idx.append(f"## {label}（{WIKI_DIR.name}/{sub}/）\n")
        if plist is None:  # topics 动态列目录
            items = [{"path": f.relative_to(WIKI_DIR).as_posix(),
                      "meta": parse_frontmatter(
                          (WIKI_DIR / sub / f.name).read_text(encoding="utf-8"))[0]}
                     for f in sorted((WIKI_DIR / sub).glob("*.md"))]
        else:
            items = plist
        for p in items:
            title = p["meta"].get("title", p["path"])
            idx.append(f"- [{title}](/{p['path']})")
        idx.append("")

    # _concepts.md：概念索引
    cidx = ["# 概念索引（lint 自动生成，勿手改）", ""]
    for p in pages["concept"]:
        sources = ", ".join(p["meta"].get("sources", [])) or "无来源"
        cidx.append(f"- **{p['meta'].get('title', '')}** → {p['path']}（来源: {sources}）")
    cidx.append("")

    _index_file().write_text("\n".join(idx), encoding="utf-8")
    _concepts_index().write_text("\n".join(cidx), encoding="utf-8")

    logger.info(f"索引重建完成: topics {len(written_topics)} 页, "
                f"graph {len(graph_lines) - 2} 节点行")
    return {"topics": len(written_topics), "dangling": dangling_all}


def detect_gaps() -> list[str]:
    """知识缺口检测：孤立节点 / 悬空引用 / bug 缺根因 / 概念缺定义"""
    pages = scan_wiki()
    name_map = _name_to_path_map(pages)
    referenced: set[str] = set()
    gaps: list[str] = []

    for ptype in ("concept", "bug"):
        for p in pages[ptype]:
            path = p["path"]
            meta = p["meta"]
            rel_paths, dangling = _resolve_related(meta, name_map)
            referenced.update(rel_paths)
            for d in dangling:
                gaps.append(f"悬空引用: {path} → '{d}'（不存在同名概念/坑）")
            if ptype == "bug" and "## 根因" not in p["body"]:
                gaps.append(f"坑缺根因: {path}")
            if ptype == "concept" and not p["definition"]:
                gaps.append(f"概念缺定义: {path}")
            if meta.get("type") == "concept" and not meta.get("sources"):
                gaps.append(f"概念无来源: {path}")

    for ptype in ("concept", "bug"):
        for p in pages[ptype]:
            if p["path"] not in referenced and not p["meta"].get("related"):
                gaps.append(f"孤立节点: {p['path']}（无 related 也未被引用）")
    return gaps


if __name__ == "__main__":
    # CLI：python -m backend.compiler.lint
    stats = rebuild_indexes()
    gaps = detect_gaps()
    print("重建:", stats)
    print(f"gaps（{len(gaps)}）:")
    for g in gaps:
        print(" -", g)
