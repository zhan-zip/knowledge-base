"""
聊天 API（M5）：POST /api/chat，SSE 流式返回

事件格式（text/event-stream，每事件一行 data: JSON）：
  {"type": "tool",   "name": "...", "args": {...}}   工具调用过程
  {"type": "delta",  "content": "..."}                正文增量
  {"type": "done",   "full": "..."}                   结束（全文）

对话双落库：user 消息流开始前入库，assistant 全文在 done 后入库
（meta 记录 service 与消息来源，M9 选区问答复用此表时可带 file/selection）
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.agent.loop import run_agent_turn
from backend.config import logger
from backend.storage import db

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: str = Field(..., description="user / assistant")
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    service: Optional[str] = Field(None, description="指定 LLM 服务，默认跟随配置")


@router.post("")
async def chat(req: ChatRequest) -> StreamingResponse:
    messages = [m.model_dump() for m in req.messages]

    async def event_stream():
        # user 消息先落库（不含 system）
        for m in messages:
            if m["role"] == "user":
                db.add_conversation(role="user", content=m["content"],
                                    meta={"service": req.service} if req.service else None)
        try:
            async for event in run_agent_turn(messages, service=req.service):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("type") == "done":
                    full = event.get("full", "")
                    if full:
                        db.add_conversation(role="assistant", content=full)
                    logger.info("聊天回合完成，已落库")
        except Exception as e:  # 生成器内异常也要以 SSE 形式告知前端
            logger.error(f"聊天流异常: {e}")
            err = json.dumps({"type": "error", "message": str(e)},
                             ensure_ascii=False)
            yield f"data: {err}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@router.get("/history")
async def chat_history(limit: int = 50) -> Dict[str, Any]:
    """对话历史（倒序存储，正序返回）"""
    rows = db.list_conversations(limit=limit)
    rows.reverse()
    return {"messages": rows}
