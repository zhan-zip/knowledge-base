# Knowledge-Base

> AI 驱动的个人知识库：LLM 编译资料 + 混合检索 + 3D 知识图谱 + 间隔重复复习。

把原始资料交给 LLM 编译成结构化的知识网络，通过聊天式 agent、图谱可视化与间隔重复复习来沉淀和调用知识。它是一个全栈 agent 应用。

## 核心功能

- **编译式知识网络**：LLM 把原始资料编译成互链的结构化 Markdown（概念 / 坑 / 主题 / 复习卡），知识可累积、可迁移、可版本管理
- **混合检索**：wiki BM25 优先 → 向量 RAG 兜底，回答强制附来源引用
- **Agent 聊天**：双阶段 tool-calling，5 个内置工具（检索知识 / 获取到期复习卡 / 生成复习卡 / 收录资料 / 编译 wiki），SSE 流式回答
- **3D 知识图谱**：图谱总览 + 三栏详情页 + 图谱小地图，节点颜色反映熟练度
- **选区 AI 问答**：选中任意文本，作为强上下文向 AI 提问
- **文本批注**：选中→注释→高亮定位，不污染原始资料
- **间隔重复复习**（前端规划中）：FSRS 调度 + 诊断式复习

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | FastAPI（纯 Python，不用 LangChain） |
| LLM | 多服务支持（DeepSeek / Claude / OpenAI 中转），配置化切换 |
| Embedding | 本地 bge-small-zh-v1.5（离线，中文友好） |
| 向量库 | ChromaDB（raw_chunks + wiki_pages 双 collection） |
| 存储 | wiki 用 Markdown + git；状态用 SQLite |
| 前端 | Vue3 + Vite + 3D 知识图谱（3d-force-graph） |

## 快速开始

需要 Python 3.13 + Node.js 24 + 一个 LLM API Key。

```bash
git clone https://github.com/zhan-zip/knowledge-base.git
cd knowledge-base
pip install -r requirements.txt

# 配置 .env（至少一个 LLM 服务）：
#   DEFAULT_LLM_SERVICE=deepseek
#   DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
#   DEEPSEEK_API_KEY=sk-xxx

uvicorn backend.main:app --reload
```

- 后端 API：`http://localhost:8000/docs`
- 健康检查：`http://localhost:8000/health`
- 前端（开发）：`cd frontend && npm install && npm run dev`

> 首次使用需下载本地 embedding 模型（bge-small-zh-v1.5，约 90MB），可用 `HF_ENDPOINT=https://hf-mirror.com` 加速。

## 目录结构

```
backend/     FastAPI 后端（agent / compiler / rag / routes）
frontend/    Vue3 + Vite 前端（graph / node 详情 / review / settings 四视图）
data/        raw 原始资料 / wiki 编译产物 / chroma 向量库 / kb.db
```

## 进度

- ✅ 已完成：数据层、RAG、编译管道、Agent 循环、3D 图谱总览、三栏详情页、选区 AI 问答、文本批注、配置管理
- 🚧 进行中：FSRS 复习页、遗忘地图
