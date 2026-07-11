"""
日志配置 — 双通道输出：stderr（控制台）+ data/logs/（文件留存）
"""

import logging
import sys
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "logs"

# 控制台格式：简洁，无时间戳
_CONSOLE_FMT = logging.Formatter("%(levelname)-7s  %(message)s")
# 文件格式：完整，带时间、模块名
_FILE_FMT = logging.Formatter(
    "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def get_logger(name: str) -> logging.Logger:
    """获取 logger（自动初始化 handlers，只执行一次）"""
    logger = logging.getLogger(name)

    if not logger.handlers:
        logger.setLevel(logging.DEBUG)

        # stderr handler — 只输出 INFO 及以上
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(logging.INFO)
        console.setFormatter(_CONSOLE_FMT)
        logger.addHandler(console)

        # 文件 handler — 输出 DEBUG 及以上，保留完整记录
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(
            LOG_DIR / "tech-doc-mcp.log", encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(_FILE_FMT)
        logger.addHandler(file_handler)

    return logger
