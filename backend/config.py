"""
配置模块：环境变量加载、路径常量、日志配置、LLM 服务配置
"""
import logging
import os
import re
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

# ===== 路径常量 =====
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
WIKI_DIR = DATA_DIR / "wiki"
LEARNING_DIR = DATA_DIR / "learning"
INSIGHTS_DIR = DATA_DIR / "insights"
CHROMA_DIR = DATA_DIR / "chroma"
DB_PATH = DATA_DIR / "kb.db"

# ===== 日志配置 =====
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

logger = logging.getLogger("config")

# ===== LLM 服务配置 =====
DEFAULT_LLM_SERVICE = os.getenv("DEFAULT_LLM_SERVICE", "deepseek")
COMPILE_SERVICE = os.getenv("COMPILE_SERVICE", DEFAULT_LLM_SERVICE)

LLM_SERVICES = {
    "deepseek": {
        "base_url": os.getenv("DEEPSEEK_BASE_URL"),
        "api_key": os.getenv("DEEPSEEK_API_KEY"),
        "model": os.getenv("DEEPSEEK_MODEL"),
        "enabled": os.getenv("DEEPSEEK_ENABLED", "true").lower() == "true"
    },
    "claude": {
        "base_url": os.getenv("CLAUDE_BASE_URL"),
        "api_key": os.getenv("CLAUDE_API_KEY"),
        "model": os.getenv("CLAUDE_MODEL"),
        "enabled": os.getenv("CLAUDE_ENABLED", "false").lower() == "true"
    },
    "openai": {
        "base_url": os.getenv("OPENAI_BASE_URL"),
        "api_key": os.getenv("OPENAI_API_KEY"),
        "model": os.getenv("OPENAI_MODEL"),
        "enabled": os.getenv("OPENAI_ENABLED", "false").lower() == "true"
    }
}

def update_env_file(updates: dict):
    """
    更新 .env 文件（保留注释和格式）
    
    Args:
        updates: 要更新的键值对 {"KEY": "value"}
    """
    env_path = PROJECT_ROOT / ".env"
    lines = []
    
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    
    # 更新已有行，收集未匹配的 key（兼容 "KEY=value" 与 "KEY = value" 两种格式）
    remaining = set(updates.keys())
    for i, line in enumerate(lines):
        match = re.match(r'^([A-Z_]+)\s*=', line)
        if match and match.group(1) in updates:
            key = match.group(1)
            lines[i] = f"{key}={updates[key]}\n"
            remaining.discard(key)
    
    # 追加新 key
    for key in remaining:
        lines.append(f"{key}={updates[key]}\n")
    
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    
    logger.info(f"已更新 .env 文件：{list(updates.keys())}")

# 启动时打印配置信息
logger.info(f"默认 LLM 服务: {DEFAULT_LLM_SERVICE}")
logger.info(f"编译专用服务: {COMPILE_SERVICE}")
enabled_services = [name for name, cfg in LLM_SERVICES.items() if cfg.get("enabled")]
logger.info(f"已启用的服务: {enabled_services}")
