"""
编译管道 schema（M4）：pydantic 模型 + LLM JSON 解析 + frontmatter + slugify

- LLM 输出契约：SummaryOutput（摘要 + 概念/坑提名，提名内含复习卡）
- DeepSeek 无严格 JSON mode：解析前剥 ```json 围栏，pydantic 校验失败由
  compile 层附错误重试一次
- frontmatter 用 pyyaml（chromadb 依赖链自带 6.0.3，零新增依赖）
- slugify：中文保留（Windows/UTF-8 文件名合法），空白与非法字符转连字符，
  英文转小写 kebab-case
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError


# ===== LLM 输出契约 =====

class FlashcardOut(BaseModel):
    """复习卡提名（概念卡或坑的诊断卡）"""
    front: str = Field(..., description="卡片正面：提问或症状")
    back: str = Field(..., description="卡片背面：答案；诊断卡则为根因与解法")


class ConceptNomination(BaseModel):
    name: str = Field(..., description="概念名（简短中文，如：双阶段循环）")
    topic: str = Field(..., description="所属主题（如：Agent 架构）")
    definition: str = Field(..., description="一句话定义")
    details: str = Field(..., description="要点展开（Markdown，可含列表）")
    related: list[str] = Field(default_factory=list,
                               description="同文档内的相关概念名/坑名")
    flashcard: FlashcardOut = Field(..., description="该概念的记忆卡")


class BugNomination(BaseModel):
    name: str = Field(..., description="坑名（症状式短语，如：ChromaDB 连接超时）")
    topic: str = Field(..., description="所属主题")
    related_concepts: list[str] = Field(default_factory=list,
                                        description="关联概念名")
    symptoms: str = Field(..., description="症状：现象与报错")
    root_cause: str = Field(..., description="根因")
    solution: str = Field(..., description="解法")
    reproduction: str = Field(default="", description="复现步骤（无可留空）")
    prevention: str = Field(..., description="预防措施")
    diagnosis_steps: list[str] = Field(default_factory=list,
                                       description="排查决策步骤（症状→判别→根因）")
    flashcard: FlashcardOut = Field(..., description="诊断卡：正面给症状，背面根因+解法")


class SummaryOutput(BaseModel):
    """单份 raw 的 LLM 编译输出（整个管道的核心契约）"""
    summary: str = Field(..., description="全文摘要（Markdown，200 字内）")
    topics: list[str] = Field(default_factory=list, description="涉及的主题名列表")
    concepts: list[ConceptNomination] = Field(default_factory=list)
    bugs: list[BugNomination] = Field(default_factory=list)


def parse_llm_json(text: str) -> dict:
    """
    解析 LLM 输出的 JSON。

    顺序策略（关键：先整段解析，再剥围栏，最后截取大括号——
    不可先非贪婪剥围栏：LLM 输出的 details 字段常含 ```python 代码块
    字面量，会误截 JSON 内部的代码块导致"找不到 JSON 对象"）：
    1) 整段直接 json.loads（绝大多数情况命中）
    2) 整体外层 ```json ... ``` 围栏（锚定首尾）
    3) 截取首个 { 到最后一个 }
    """
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fence = re.match(r"^```(?:json)?\s*(.+?)\s*```\s*$", text, re.S)
    if fence:
        text = fence.group(1).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError("输出中找不到 JSON 对象")


def validate_summary_output(text: str) -> SummaryOutput:
    """LLM 文本 → SummaryOutput（不合法抛 ValidationError/ValueError）"""
    return SummaryOutput.model_validate(parse_llm_json(text))


# ===== slugify =====

_ILLEGAL_FILE_RE = re.compile(r'[\\/:*?"<>|\s]+')


def slugify(name: str) -> str:
    """文件名 slug：中文保留，空白/非法字符转 '-'，英文小写（kebab-case）"""
    s = name.strip().lower()
    s = _ILLEGAL_FILE_RE.sub("-", s).strip("-.")
    return s or "untitled"


# ===== frontmatter =====

def to_frontmatter(meta: dict, body: str) -> str:
    """序列化 wiki 页：--- YAML frontmatter --- + 正文"""
    fm = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
    return f"---\n{fm}---\n\n{body.strip()}\n"


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 wiki 页 → (frontmatter dict, 正文)；无 frontmatter 时返回 ({}, 原文)"""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, text
    return meta, parts[2].strip()


def new_page_meta(title: str, page_type: str, sources: list[str],
                  topic: str = "", related: Optional[list[str]] = None,
                  tags: Optional[list[str]] = None) -> dict:
    """wiki 页 frontmatter 统一构造（保持字段顺序稳定，便于 diff 与解析）"""
    meta = {
        "title": title,
        "type": page_type,                       # summary / concept / bug / topic
        "topic": topic,
        "sources": sources,                      # raw 文件相对路径列表
        "related": related or [],
        "tags": tags or [],
        "created": date.today().isoformat(),
    }
    return meta


WikiType = Literal["summary", "concept", "bug", "topic"]


__all__ = [
    "SummaryOutput", "ConceptNomination", "BugNomination", "FlashcardOut",
    "parse_llm_json", "validate_summary_output", "ValidationError",
    "slugify", "to_frontmatter", "parse_frontmatter", "new_page_meta",
]
