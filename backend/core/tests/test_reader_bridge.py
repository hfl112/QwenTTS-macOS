"""C5(计划 #10)——URL-Reader 接入 adapter 的回归测试。"""

import os
import sys

from core import reader_bridge


def test_ensure_path_is_idempotent_single_entry():
    """import/调用多次,sys.path 里 URL-Reader 至多一条(历史 6 处内联块
    insert/append 混用,可能插多条)。"""
    reader_bridge._ensure_path()
    reader_bridge._ensure_path()
    assert sys.path.count(reader_bridge.reader_dir()) == 1
    assert os.path.basename(reader_bridge.reader_dir()) == "URL-Reader"


def test_bridge_does_not_import_mlx_or_llm_sdks():
    """bridge 本体是惰性的:import 它不应引入 mlx / LLM SDK。"""
    assert "mlx" not in sys.modules
    assert "google.generativeai" not in sys.modules


def test_default_engines_returns_isolated_copy():
    """default_engines 返回深拷贝:调用方改返回值不污染单一真相。"""
    a = reader_bridge.default_engines()
    assert isinstance(a, dict) and a  # schema 非空
    top_key = next(iter(a))
    a[top_key] = "污染"
    b = reader_bridge.default_engines()
    assert b[top_key] != "污染"


def test_backend_has_no_inline_reader_syspath_blocks():
    """backend.py 不再内联拼 URL-Reader 路径(全部走 bridge)。"""
    backend_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend.py"
    )
    with open(backend_path, "r", encoding="utf-8") as f:
        src = f.read()
    assert 'os.path.join(BASE_DIR, "URL-Reader")' not in src
    assert "from reader_service import" not in src
    assert "from llm_engine import" not in src
    assert "from engine_config import" not in src


def test_bridge_injects_data_path_into_engine_config():
    """C5.2:engine_config 的 config 路径 = core/paths 的解析结果(注入,
    不再自行重推 App Support)。"""
    from core.paths import runtime_paths

    reader_bridge._ensure_path()
    import engine_config

    assert engine_config.config_path() == os.path.join(
        runtime_paths.data_path, "config.json"
    )


def test_deep_merge_engines_returns_copy_not_inplace():
    """C5.2:合并返回新 dict、不改入参(backend 旧实现是 in-place,已删)。"""
    base = {"a": {"b": 1}}
    merged = reader_bridge.deep_merge_engines(base, {"a": {"c": 2}})
    assert merged == {"a": {"b": 1, "c": 2}}
    assert base == {"a": {"b": 1}}
