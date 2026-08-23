"""
LLM 客户端：多服务支持 + 统一调用接口

参考 qq-agent/llm/llm.py，简化为知识库项目需要的功能：
- 多服务支持（deepseek / claude / openai）
- 统一的 chat_completion 接口
- 服务测试和切换
"""
import asyncio
import logging
from typing import Dict, List, Optional, Any

from openai import OpenAI

from backend.config import LLM_SERVICES, DEFAULT_LLM_SERVICE

logger = logging.getLogger("llm")


class LLMClient:
    """
    多 LLM 服务客户端
    
    用法：
        client = LLMClient()
        response = await client.chat_completion(
            messages=[{"role": "user", "content": "Hello"}],
            service="deepseek"  # 可选，不指定则用默认服务
        )
    """
    
    def __init__(self):
        """初始化所有已启用的 LLM 服务客户端"""
        self.clients: Dict[str, OpenAI] = {}
        
        for service_name, config in LLM_SERVICES.items():
            if not config.get("enabled"):
                continue
            
            base_url = config.get("base_url")
            api_key = config.get("api_key")
            
            if not base_url or not api_key:
                logger.warning(f"服务 {service_name} 配置不完整，跳过")
                continue
            
            try:
                self.clients[service_name] = OpenAI(
                    base_url=base_url,
                    api_key=api_key
                )
                logger.info(f"LLM 服务 {service_name} 初始化成功")
            except Exception as e:
                logger.error(f"LLM 服务 {service_name} 初始化失败: {e}")
        
        if not self.clients:
            logger.warning("没有可用的 LLM 服务！")
    
    def get_client(self, service: Optional[str] = None) -> Optional[OpenAI]:
        """
        获取指定服务的客户端
        
        Args:
            service: 服务名称，不指定则使用默认服务
            
        Returns:
            OpenAI 客户端实例，失败返回 None
        """
        service_name = service or DEFAULT_LLM_SERVICE
        client = self.clients.get(service_name)
        
        if not client:
            logger.error(f"服务 {service_name} 不可用")
            return None
        
        return client
    
    def get_model(self, service: Optional[str] = None) -> Optional[str]:
        """
        获取指定服务的模型名称
        
        Args:
            service: 服务名称，不指定则使用默认服务
            
        Returns:
            模型名称，失败返回 None
        """
        service_name = service or DEFAULT_LLM_SERVICE
        config = LLM_SERVICES.get(service_name)
        
        if not config:
            logger.error(f"服务 {service_name} 配置不存在")
            return None
        
        return config.get("model")
    
    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
        service: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> Any:
        """
        调用 LLM 进行对话补全
        
        Args:
            messages: 消息列表 [{"role": "user", "content": "..."}]
            tools: 工具定义（OpenAI function calling 格式）
            stream: 是否流式输出
            service: 使用的服务名称
            temperature: 温度参数
            max_tokens: 最大 token 数
            
        Returns:
            非流式：返回 ChatCompletion 对象
            流式：返回生成器
        """
        client = self.get_client(service)
        model = self.get_model(service)
        
        if not client or not model:
            raise ValueError(f"服务 {service or DEFAULT_LLM_SERVICE} 不可用")
        
        service_name = service or DEFAULT_LLM_SERVICE
        logger.info(f"调用 LLM: {service_name}/{model}, messages={len(messages)}, tools={len(tools) if tools else 0}")
        
        try:
            kwargs = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "stream": stream
            }
            
            if tools:
                kwargs["tools"] = tools
            
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            
            # 在异步环境中调用同步的 OpenAI 客户端
            response = await asyncio.to_thread(
                lambda: client.chat.completions.create(**kwargs)
            )
            
            return response
            
        except Exception as e:
            logger.error(f"LLM 调用失败 ({service_name}/{model}): {e}")
            raise
    
    async def test_service(self, service: str) -> Dict[str, Any]:
        """
        测试指定服务的连接
        
        Args:
            service: 服务名称
            
        Returns:
            {"success": bool, "message": str, "model": str}
        """
        try:
            response = await self.chat_completion(
                messages=[{"role": "user", "content": "Hi"}],
                service=service,
                max_tokens=10
            )
            
            content = response.choices[0].message.content if response.choices else ""
            model = self.get_model(service)
            
            return {
                "success": True,
                "message": f"连接成功，模型响应：{content[:50]}",
                "model": model
            }
            
        except Exception as e:
            return {
                "success": False,
                "message": f"连接失败: {str(e)}",
                "model": self.get_model(service)
            }


# 全局单例
_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """获取全局 LLM 客户端单例"""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
