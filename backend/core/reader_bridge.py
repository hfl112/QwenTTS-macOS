"""URL-Reader 引擎层的唯一接入 adapter(计划 #10 C5)。

URL-Reader 不是包(靠 sys.path 才能 import)。此前 backend.py 在 6 个调用点
各自内联「拼 reader_dir → 改 sys.path(insert/append 还不一致)→ 函数内 import」;
收口到本模块:路径只配置一次,调用方拿到显式函数,测试可在此 seam 替换。
真实 import 保持惰性(函数内),不在 web 进程启动时强加载 LLM SDK。
"""
from __future__ import annotations

import copy
import os
import sys
from typing import Any

_READER_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "URL-Reader"
)


def reader_dir() -> str:
    return _READER_DIR


_configured = False


def _ensure_path() -> None:
    """幂等:sys.path 里最多一条 URL-Reader 记录(统一 insert(0),优先命中);
    并把 core/paths 解析好的 data path 注入 engine_config(一次),消除其
    与 core/paths.py 的重复路径推导。"""
    global _configured
    if _READER_DIR not in sys.path:
        sys.path.insert(0, _READER_DIR)
    if not _configured:
        from core.paths import runtime_paths

        import engine_config

        engine_config.set_data_path(runtime_paths.data_path)
        _configured = True


def process_with_llm(text: str, mode: str) -> str:
    _ensure_path()
    from reader_service import process_with_llm as fn

    return fn(text, mode)


def process_url_job(**kwargs: Any):
    _ensure_path()
    from reader_service import process_url_job as fn

    return fn(**kwargs)


def llm_selected_available() -> bool:
    _ensure_path()
    from llm_engine import llm_selected_available as fn

    return fn()


def probe(family: str, provider: str) -> tuple[bool, str]:
    """连通性探活:family='llm' 走 llm_engine,其余走 translation_engine。"""
    _ensure_path()
    if family == "llm":
        from llm_engine import probe_provider
    else:
        from translation_engine import probe_provider

    return probe_provider(provider)


def default_engines() -> dict[str, Any]:
    """engines 配置默认 schema(单一真相在 URL-Reader/engine_config.py)。"""
    _ensure_path()
    from engine_config import DEFAULT_ENGINES

    return copy.deepcopy(DEFAULT_ENGINES)


def normalize_mode(mode: str | None) -> str:
    """mode 词表规范化(N1 改名:podcast-discuss→dual-summary 等,见 URL-Reader/modes.py)。"""
    _ensure_path()
    from modes import normalize_mode as fn

    return fn(mode)


def legacy_mode_equivalents(mode: str) -> list[str]:
    _ensure_path()
    from modes import legacy_equivalents as fn

    return fn(mode)


def deep_merge_engines(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """engines 配置深合并(返回新 dict,不改入参)。单一实现在 engine_config;
    backend 此前自带一份 in-place 版,已删。"""
    _ensure_path()
    from engine_config import deep_merge

    return deep_merge(base, override)
