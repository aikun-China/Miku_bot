"""
MikuBot 依赖配置统一管理

从 requirements.txt 读取依赖信息，集中管理路径配置。
"""

from pathlib import Path
import os
import sys
import re

# ─── 项目路径 ───
BASE_DIR = Path(__file__).resolve().parent.parent
VENV_DIR = BASE_DIR / ".venv"
VENV_PYTHON = VENV_DIR / "Scripts" / "python.exe"
VENV_PIP = VENV_DIR / "Scripts" / "pip.exe"
REQUIREMENTS_FILE = BASE_DIR / "requirements.txt"


def get_python_deps() -> dict:
    """
    从 requirements.txt 解析依赖列表
    返回 {包名: 导入名} 字典
    """
    deps = {}
    
    if not REQUIREMENTS_FILE.exists():
        print(f"[WARN] {REQUIREMENTS_FILE} 不存在")
        return deps
    
    content = REQUIREMENTS_FILE.read_text(encoding="utf-8")
    
    for line in content.splitlines():
        line = line.strip()
        
        # 跳过空行和注释
        if not line or line.startswith("#"):
            continue
        
        # 提取包名（去掉版本号和可选依赖）
        # 例如: nonebot2>=2.5.0 -> nonebot2
        #       nonebot-adapter-onebot[extra]>=2.4.0 -> nonebot-adapter-onebot
        match = re.match(r'^([a-zA-Z0-9_-]+)', line)
        if match:
            pip_name = match.group(1)
            
            # 将 pip 包名转换为导入名（替换 - 为 _）
            import_name = pip_name.replace("-", "_")
            
            # 特殊映射（pip 名和导入名不同的情况）
            special_mapping = {
                "nonebot2": "nonebot",
                "nonebot_adapter_onebot": "nonebot.adapters.onebot",
                "PyYAML": "yaml",
                "pyyaml": "yaml",
                "APScheduler": "apscheduler",
                "apscheduler": "apscheduler",
            }
            import_name = special_mapping.get(import_name, import_name)
            
            deps[pip_name] = import_name
    
    return deps


def ensure_requirements_file():
    """确保 requirements.txt 文件存在"""
    if not REQUIREMENTS_FILE.exists():
        print(f"[ERROR] {REQUIREMENTS_FILE} 不存在，请创建该文件")
        return False
    return True