# MikuBot 工具模块
import json
import os
from pathlib import Path

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent

# 数据目录
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# 配置目录
CONFIG_DIR = BASE_DIR / "config"
CONFIG_DIR.mkdir(exist_ok=True)

# 日志目录
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)


def load_json(filename: str, default=None):
    """加载 JSON 文件"""
    filepath = DATA_DIR / f"{filename}.json"
    if filepath.exists():
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return default or {}


def save_json(filename: str, data: dict):
    """保存 JSON 文件"""
    filepath = DATA_DIR / f"{filename}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_config_path(name: str) -> Path:
    """获取配置文件路径"""
    return CONFIG_DIR / f"{name}.yaml"
