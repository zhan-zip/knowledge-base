"""
LLM 服务配置管理 API

提供多 LLM 服务的配置查询、更新、测试功能
"""
import logging
from typing import Dict, Any, List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import LLM_SERVICES, DEFAULT_LLM_SERVICE, COMPILE_SERVICE, update_env_file
from backend.agent.llm import get_llm_client

logger = logging.getLogger("services")

router = APIRouter(prefix="/api/services", tags=["services"])


# ===== 数据模型 =====

class ServiceConfig(BaseModel):
    """单个服务配置"""
    name: str
    enabled: bool
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


class ServicesUpdateRequest(BaseModel):
    """服务配置更新请求"""
    default_service: str | None = None
    compile_service: str | None = None
    services: Dict[str, ServiceConfig] | None = None


# ===== API 端点 =====

@router.get("")
async def get_services() -> Dict[str, Any]:
    """
    获取所有 LLM 服务配置（读 .env 磁盘值，即"已保存"的配置）

    注意：与内存中正在生效的 LLM_SERVICES 可能不同（保存后需重启后端），
    页面展示磁盘值 + "需重启生效"提示，语义自洽；test 端点测的是内存生效配置。
    """
    from dotenv import dotenv_values

    from backend.config import PROJECT_ROOT

    logger.info("查询服务配置（.env 磁盘值）")
    env = dotenv_values(PROJECT_ROOT / ".env")

    def mask(key: str | None) -> str:
        if not key:
            return ""
        return key[:4] + "***" if len(key) > 4 else "***"

    def build(prefix: str) -> Dict[str, Any]:
        return {
            "enabled": str(env.get(f"{prefix}_ENABLED", "false")).lower() == "true",
            "base_url": env.get(f"{prefix}_BASE_URL") or "",
            "api_key": mask(env.get(f"{prefix}_API_KEY")),
            "model": env.get(f"{prefix}_MODEL") or "",
        }

    default_service = env.get("DEFAULT_LLM_SERVICE") or "deepseek"
    return {
        "default_service": default_service,
        "compile_service": env.get("COMPILE_SERVICE") or default_service,
        "services": {
            "deepseek": build("DEEPSEEK"),
            "claude": build("CLAUDE"),
            "openai": build("OPENAI"),
        },
    }


@router.put("")
async def update_services(request: ServicesUpdateRequest) -> Dict[str, Any]:
    """
    更新服务配置（写入 .env 文件）
    
    请求体示例：
    {
        "default_service": "deepseek",
        "compile_service": "claude",
        "services": {
            "deepseek": {
                "name": "deepseek",
                "enabled": true,
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "sk-xxx",
                "model": "deepseek-chat"
            }
        }
    }
    """
    logger.info(f"更新服务配置: {request.model_dump()}")
    
    updates = {}
    
    # 更新默认服务
    if request.default_service:
        updates["DEFAULT_LLM_SERVICE"] = request.default_service
    
    # 更新编译服务
    if request.compile_service:
        updates["COMPILE_SERVICE"] = request.compile_service
    
    # 更新各服务配置
    if request.services:
        for service_name, config in request.services.items():
            prefix = service_name.upper()
            
            if config.enabled is not None:
                updates[f"{prefix}_ENABLED"] = "true" if config.enabled else "false"
            
            if config.base_url:
                updates[f"{prefix}_BASE_URL"] = config.base_url
            
            if config.api_key and "***" not in config.api_key:
                # 脱敏值（sk-x*** 形式）不回写，防止覆盖真实密钥
                updates[f"{prefix}_API_KEY"] = config.api_key
            
            if config.model:
                updates[f"{prefix}_MODEL"] = config.model
    
    try:
        update_env_file(updates)
        return {"success": True, "message": f"已更新 {len(updates)} 个配置项"}
    except Exception as e:
        logger.error(f"更新配置失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新配置失败: {e}")


@router.get("/{service}/models")
async def get_service_models(service: str) -> Dict[str, Any]:
    """
    获取指定服务的可用模型列表（动态调用 /v1/models）

    成功：{"service", "models": [id...], "current_model", "dynamic": true}
    失败（网络/凭据/端点不支持）：{"service", "models": [], "current_model",
        "dynamic": false, "error": "..."} —— 前端据此降级为手动输入框
    """
    import asyncio

    logger.info(f"查询服务 {service} 的模型列表")

    config = LLM_SERVICES.get(service)
    if not config:
        raise HTTPException(status_code=404, detail=f"服务 {service} 不存在")

    try:
        client = get_llm_client().get_client(service)
        if not client:
            raise ValueError(f"服务 {service} 未启用或配置不完整")
        models = await asyncio.to_thread(
            lambda: sorted(m.id for m in client.models.list()))
        return {"service": service, "models": models,
                "current_model": config.get("model"), "dynamic": True}
    except Exception as e:
        logger.warning(f"动态获取 {service} 模型列表失败: {e}")
        return {"service": service, "models": [],
                "current_model": config.get("model"),
                "dynamic": False, "error": str(e)[:200]}


@router.post("/{service}/test")
async def test_service(service: str) -> Dict[str, Any]:
    """
    测试指定服务的连接
    
    返回格式：
    {
        "success": true,
        "service": "deepseek",
        "model": "deepseek-chat",
        "message": "连接成功，模型响应：Hello! ..."
    }
    """
    logger.info(f"测试服务 {service} 连接")
    
    if service not in LLM_SERVICES:
        raise HTTPException(status_code=404, detail=f"服务 {service} 不存在")
    
    try:
        client = get_llm_client()
        result = await client.test_service(service)
        
        return {
            "service": service,
            **result
        }
    except Exception as e:
        logger.error(f"测试服务 {service} 失败: {e}")
        return {
            "service": service,
            "success": False,
            "message": f"测试失败: {str(e)}",
            "model": LLM_SERVICES.get(service, {}).get("model")
        }
