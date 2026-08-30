"""
Agent 主循环（M5）：双阶段 tool-calling（复用 QQ agent 成熟模式）

阶段 1（非流式）：带 tools 调用 → 判断是否需要工具
  - 无 tool_calls → 直接把 content 作为完整 delta 事件输出（省一次调用）
  - 有 tool_calls → 逐个执行（yield tool 事件）→ 结果以 tool 消息回填
阶段 2（流式）：带工具结果二次调用，stream=True，逐 chunk yield delta 事件

输出为事件流（async generator），由 routes/chat.py 包装为 SSE：
  {"type": "tool", "name": ..., "args": ...}   工具调用过程（前端可展示）
  {"type": "delta", "content": "..."}           正文增量
  {"type": "done", "full": "..."}               结束（含全文，供落库/展示）

V1 限制：最多一轮工具调用（设计文档的双阶段定义）；多轮工具链留待后续。
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncGenerator

from backend.agent.llm import get_llm_client
from backend.agent.tools import TOOL_SCHEMAS, execute_tool
from backend.config import logger

SYSTEM_PROMPT = (
    "你是个人知识库管家。用户的知识库收录了他做 agent 项目时积累的经验"
    "（概念 wiki、踩坑记录、复习卡）。\n"
    "行为准则：\n"
    "1. 回答知识类问题前，先用 search_knowledge 检索知识库，"
    "回答末尾用「来源：xxx」标注引用的文件。\n"
    "2. 检索不到时如实告知知识库中没有，不要编造。\n"
    "3. 用户想复习时调用 get_due_reviews，把卡面问题抛给用户作答。\n"
    "4. 用户要求收录资料或重新编译时调用对应工具。\n"
    "5. 回答用中文，简洁直接。"
)


def _assistant_tool_msg(message) -> dict:
    """阶段 1 的 assistant 消息（含 tool_calls）原样转 dict，回填阶段 2"""
    return {
        "role": "assistant",
        "content": message.content or "",
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name,
                             "arguments": tc.function.arguments},
            } for tc in message.tool_calls
        ],
    }


async def _stream_second_pass(messages: list[dict],
                              service: str | None) -> AsyncGenerator[str, None]:
    """阶段 2 流式调用：同步 Stream 迭代器放到线程池逐块拉取"""
    client = get_llm_client()
    stream = await client.chat_completion(messages=messages, tools=TOOL_SCHEMAS,
                                          stream=True, service=service)
    while True:
        chunk = await asyncio.to_thread(next, stream, None)
        if chunk is None:
            break
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        content = getattr(delta, "content", None)
        if content:
            yield content


async def run_agent_turn(messages: list[dict],
                         service: str | None = None,
                         context: str | None = None) -> AsyncGenerator[dict, None]:
    """
    一轮 agent 对话：messages 为前端传来的历史（[{role, content}]）。
    自动注入 system prompt；context 为附加上下文（如当前查看的节点，
    M8 详情页聊天携带），拼接到 system prompt 尾部。
    产出事件流（见模块 docstring）。
    """
    system_content = SYSTEM_PROMPT + (f"\n\n【当前上下文】{context}" if context else "")
    if messages and messages[0].get("role") == "system":
        convo = list(messages)  # 兼容：调用方自带 system 则原样使用
    else:
        convo = [{"role": "system", "content": system_content}] + list(messages)

    client = get_llm_client()
    try:
        first = await client.chat_completion(messages=convo, tools=TOOL_SCHEMAS,
                                             stream=False, service=service)
    except Exception as e:
        logger.error(f"Agent 阶段 1 调用失败: {e}")
        yield {"type": "delta", "content": f"LLM 调用失败：{e}"}
        yield {"type": "done", "full": f"LLM 调用失败：{e}"}
        return

    msg = first.choices[0].message

    # 无工具 → 直接输出阶段 1 内容
    if not msg.tool_calls:
        text = msg.content or ""
        if text:
            yield {"type": "delta", "content": text}
        yield {"type": "done", "full": text}
        return

    # 有工具 → 执行并回填
    convo.append(_assistant_tool_msg(msg))
    for tc in msg.tool_calls:
        name = tc.function.name
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        yield {"type": "tool", "name": name, "args": args}
        result = await execute_tool(name, args)
        convo.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    # 阶段 2：流式生成最终回答
    collected: list[str] = []
    try:
        async for piece in _stream_second_pass(convo, service):
            collected.append(piece)
            yield {"type": "delta", "content": piece}
    except Exception as e:
        logger.error(f"Agent 阶段 2 流式失败: {e}")
        piece = f"\n\n[流式输出中断：{e}]"
        collected.append(piece)
        yield {"type": "delta", "content": piece}

    full_text = "".join(collected)
    yield {"type": "done", "full": full_text}
