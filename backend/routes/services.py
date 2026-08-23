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
    获取所有 LLM 服务配置
    
    返回格式：
    {
        "default_service": "deepseek",
        "compile_service": "deepseek",
        "services": {
            "deepseek": {
                "enabled": true,
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "***",  # 脱敏
                "model": "deepseek-chat"
            },
            ...
        }
    }
    """
    logger.info("查询服务配置")
    
    # 脱敏处理：API key 只显示前4位
    services_masked = {}
    for name, config in LLM_SERVICES.items():
        masked_config = dict(config)
        api_key = masked_config.get("api_key", "")
        if api_key:
            masked_config["api_key"] = api_key[:4] + "***" if len(api_key) > 4 else "***"
        services_masked[name] = masked_config
    
    return {
        "default_service": DEFAULT_LLM_SERVICE,
        "compile_service": COMPILE_SERVICE,
        "services": services_masked
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
            
            if config.api_key and config.api_key != "***":  # 只更新非脱敏值
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
    获取指定服务的可用模型列表
    
    注意：目前返回配置的固定模型，未来可扩展为动态查询
    """
    logger.info(f"查询服务 {service} 的模型列表")
    
    config = LLM_SERVICES.get(service)
    if not config:
        raise HTTPException(status_code=404, detail=f"服务 {service} 不存在")
    
    model = config.get("model")
    
    # 返回当前配置的模型（未来可扩展为调用 API 获取模型列表）
    return {
        "service": service,
        "models": [model] if model else [],
        "current_model": model
    }


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
