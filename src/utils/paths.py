"""路径解析：把项目内的相对资源路径统一解析为绝对路径。

支持 PyInstaller 冻结环境，不依赖任何机器绝对路径常量。
"""
from __future__ import annotations

import os
import sys

# src/utils/paths.py -> 项目根目录
_THIS = os.path.abspath(__file__)
SRC_DIR = os.path.dirname(os.path.dirname(_THIS))
ROOT_DIR = os.path.dirname(SRC_DIR)


def project_root() -> str:
    """返回项目根目录（打包运行时返回可执行文件所在目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return ROOT_DIR


def resource_path(*parts: str) -> str:
    """项目内资源路径，例如 resource_path('data', 'default_map.json')。"""
    return os.path.join(project_root(), *parts)


def data_path(*parts: str) -> str:
    return resource_path("data", *parts)


def config_path(*parts: str) -> str:
    return resource_path("config", *parts)


def assets_path(*parts: str) -> str:
    return resource_path("assets", *parts)


def saves_path(*parts: str) -> str:
    return resource_path("saves", *parts)


def logs_path(*parts: str) -> str:
    return resource_path("logs", *parts)


def ensure_dir(path: str) -> str:
    """确保目录存在并返回该目录。"""
    os.makedirs(path, exist_ok=True)
    return path
