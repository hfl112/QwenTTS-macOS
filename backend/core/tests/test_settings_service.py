"""M5(计划 #13)——SettingsService:配置读写的唯一拥有者。

此前语义散在 ASGI 层:/settings 路由直接 update dict、/engines* 三条路由各自
read-merge-write、/engines/check 在路由体里改嵌套 dict 写凭证。收口后路由
validate→调本服务→return,凭证/合并/profile 解析可经接口测试。
"""

import tempfile

from core.services.settings_service import SettingsService
from core.storage import Storage


def _svc() -> SettingsService:
    tmp = tempfile.mkdtemp()
    return SettingsService(Storage(data_dir=tmp))


def test_update_settings_filters_none_and_persists():
    svc = _svc()
    cfg = svc.update_settings({"voice": "Ryan", "speed": None})
    assert cfg["voice"] == "Ryan"
    assert "speed" not in cfg or cfg["speed"] is not None  # None 不落盘
    assert svc.get_settings()["voice"] == "Ryan"  # 持久化


def test_resolved_profile_priority():
    """profile 解析:请求 override > config 存值 > balanced(读路径默认)。"""
    svc = _svc()
    assert svc.resolved_profile() == "balanced"
    svc.update_settings({"performance_profile": "quiet"})
    assert svc.resolved_profile() == "quiet"
    assert svc.resolved_profile("fast") == "fast"


def test_get_engines_merges_defaults_over_partial_store():
    svc = _svc()
    svc.update_engines({"llm": {"selected": "claude"}})
    merged = svc.get_engines()
    assert merged["llm"]["selected"] == "claude"
    # 默认 schema 的其它段(translate 等)必须仍然出现
    assert "translate" in merged


def test_store_engine_credential_llm_and_translate():
    svc = _svc()
    # LLM key → keys[provider];不改 selected
    before_selected = svc.get_engines()["llm"].get("selected")
    assert svc.store_engine_credential("llm", "claude", "sk-test") is True
    eng = svc.get_engines()
    assert eng["llm"]["keys"]["claude"] == "sk-test"
    assert eng["llm"].get("selected") == before_selected
    # local → local_model_path
    svc.store_engine_credential("llm", "local", "/models/x")
    assert svc.get_engines()["llm"]["local_model_path"] == "/models/x"
    # microsoft 带 region
    svc.store_engine_credential("translate", "microsoft", "mk", region="eastasia")
    tr = svc.get_engines()["translate"]
    assert tr["microsoft_key"] == "mk" and tr["microsoft_region"] == "eastasia"
    # deepl
    svc.store_engine_credential("translate", "deepl", "dk")
    assert svc.get_engines()["translate"]["deepl_key"] == "dk"
    # 无 key → 不写
    assert svc.store_engine_credential("llm", "claude", None) is False
