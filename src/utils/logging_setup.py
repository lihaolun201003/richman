"""日志配置：同时输出到控制台和 logs/ 目录下的文件。"""
from __future__ import annotations

import datetime as _dt
import logging
import os
import sys

from .paths import ensure_dir, logs_path

_configured = False


def setup_logging(level: int = logging.INFO, filename: str | None = None) -> logging.Logger:
    """配置根日志器，重复调用无副作用。"""
    global _configured
    root = logging.getLogger()
    if _configured:
        return root

    root.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"
    )

    # 控制台：GBK 环境下中文可能报错，强制 utf-8 并忽略无法编码的字符
    try:
        stream = sys.stdout
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
        sh = logging.StreamHandler(stream)
        sh.setFormatter(fmt)
        root.addHandler(sh)
    except Exception:  # pragma: no cover - 极端环境兜底
        pass

    try:
        ensure_dir(logs_path())
        name = filename or f"richman-{_dt.date.today().isoformat()}.log"
        fh = logging.FileHandler(os.path.join(logs_path(), name), encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception:  # pragma: no cover
        pass

    _configured = True
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
