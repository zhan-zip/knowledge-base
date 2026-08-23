"""
FastAPI 主应用：REST API + CORS + 静态文件托管
"""
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

logger = logging.getLogger("main")

# 创建 FastAPI 应用
app = FastAPI(
    title="Knowledge Base API",
    description="Personal AI Knowledge Base API",
    version="0.1.0"
)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发环境允许所有来源，生产环境应限制
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== 健康检查端点 =====
@app.get("/health")
async def health_check():
    """健康检查接口"""
    logger.info("Health check called")
    return JSONResponse(
        content={
            "status": "ok",
            "service": "knowledge-base",
            "version": "0.1.0"
        },
        status_code=200
    )

# ===== 根路径 =====
@app.get("/")
async def root():
    """根路径"""
    return {"message": "Knowledge Base API", "docs": "/docs"}

# ===== 静态文件托管（前端构建产物）=====
# 注意：frontend/dist/ 目前不存在，等 M6 前端构建后生效
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
    logger.info(f"静态文件托管已启用: {frontend_dist}")

# 启动日志
logger.info("Knowledge Base API 已加载")
logger.info("访问 http://localhost:8000/docs 查看 API 文档")
logger.info("访问 http://localhost:8000/health 进行健康检查")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
