"""
配置加载 — 读取 config.yaml，解析环境变量，自动加载 .env
"""

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv as _load_dotenv


# 默认配置文件路径（项目根目录）
CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.yaml"
ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"

# 模块加载时自动读取 .env（项目级配置覆盖全局环境变量）
_load_dotenv(ENV_PATH, override=True)


def _resolve_env(value: Any) -> Any:
    """递归解析配置值中的 ${ENV_VAR} 占位符"""
    if isinstance(value, str):
        # 匹配 ${VAR_NAME} 格式
        def replacer(m: re.Match) -> str:
            var_name = m.group(1)
            return os.environ.get(var_name, m.group(0))
        return re.sub(r"\$\{(\w+)\}", replacer, value)
    elif isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_env(v) for v in value]
    return value


def load_config(path: Path | str | None = None) -> dict:
    """加载并返回完整配置

    Args:
        path: 配置文件路径，默认使用项目根目录的 config.yaml

    Returns:
        解析后的配置字典
    """
    config_path = Path(path) if path else CONFIG_PATH

    if not config_path.exists():
        raise FileNotFoundError(
            f"配置文件不存在: {config_path}\n"
            f"请复制 config.yaml.example 为 config.yaml 并填入你的 API 信息"
        )

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raise ValueError(f"配置文件为空: {config_path}")

    return _resolve_env(raw)


def get_llm_config(config: dict | None = None) -> dict[str, str]:
    """获取 LLM 配置"""
    if config is None:
        config = load_config()
    return config["llm"]


def get_embedding_config(config: dict | None = None) -> dict[str, str | int]:
    """获取 Embedding 配置"""
    if config is None:
        config = load_config()
    return config["embedding"]
