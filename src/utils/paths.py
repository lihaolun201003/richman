"""路径解析：把项目内的相对资源路径统一解析为绝对路径。

打包（PyInstaller）后有两类目录，必须分开：

- **只读资源**（data/、assets/、config/default.json）：随包发布，
  onedir 模式下位于可执行文件旁的 `_internal/`，用 `resource_root()` 定位。
- **可写数据**（saves/、logs/、config/user_settings.json）：必须写在
  可执行文件旁边，否则重启就丢，用 `project_root()` 定位。

不依赖任何机器绝对路径常量。
"""
from __future__ import annotations

import os
import sys

# src/utils/paths.py -> 项目根目录
_THIS = os.path.abspath(__file__)
SRC_DIR = os.path.dirname(os.path.dirname(_THIS))
ROOT_DIR = os.path.dirname(SRC_DIR)

_IS_FROZEN = bool(getattr(sys, "frozen", False))


def is_frozen() -> bool:
    return _IS_FROZEN


def project_root() -> str:
    """可写数据的根目录（存档 / 用户设置 / 日志）。

    开发时是项目根目录；打包后是可执行文件所在目录。
    """
    if _IS_FROZEN:
        return os.path.dirname(os.path.abspath(sys.executable))
    return ROOT_DIR


def resource_root() -> str:
    """只读资源的根目录（data / assets / 默认配置）。

    打包后 PyInstaller 会把 --add-data 的内容放到 sys._MEIPASS
    （onefile 是临时解压目录，onedir 是 _internal），必须从这里读。
    """
    if _IS_FROZEN:
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return ROOT_DIR


def resource_path(*parts: str) -> str:
    """只读资源路径，例如 resource_path('data', 'default_map.json')。"""
    return os.path.join(resource_root(), *parts)


def data_path(*parts: str) -> str:
    return resource_path("data", *parts)


def assets_path(*parts: str) -> str:
    return resource_path("assets", *parts)


def default_config_path() -> str:
    """只读的默认配置（config/default.json）。"""
    return resource_path("config", "default.json")


def user_config_dir() -> str:
    """可写的配置目录（存放 user_settings.json）。"""
    return os.path.join(project_root(), "config")


def config_path(*parts: str) -> str:
    """兼容旧调用：默认指向只读资源目录下的 config。"""
    return resource_path("config", *parts)


def saves_path(*parts: str) -> str:
    return os.path.join(project_root(), "saves", *parts)


def logs_path(*parts: str) -> str:
    return os.path.join(project_root(), "logs", *parts)


def ensure_dir(path: str) -> str:
    """确保目录存在并返回该目录。"""
    os.makedirs(path, exist_ok=True)
    return path
