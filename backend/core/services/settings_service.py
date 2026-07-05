"""SettingsService — 配置读写的唯一拥有者(M5,计划 #13)。

此前语义散在 ASGI 层:/settings 路由直接 update dict、/engines* 三条路由各自
read-merge-write、/engines/check 在路由体里 30 行改嵌套 dict 写凭证;
performance_profile 的 "balanced" 回退被手抄多处(read_orchestrator ×2)。
收口后:路由 validate→调本服务→return;凭证写入/合并/默认值/profile 解析
住在一个模块,经接口可测(test_settings_service)。

注:podcast 路径的 "quiet" 默认属播客域语义,住在
podcast_service.prepare_podcast_config,不归这里。
"""
from __future__ import annotations

from typing import Any

from core import reader_bridge

# 读路径的 performance_profile 默认档(播客默认 quiet 在 podcast 域)
DEFAULT_READ_PROFILE = "balanced"


class SettingsService:
    def __init__(self, storage: Any) -> None:
        self.storage = storage

    # ------------------------------------------------------------- /settings
    def get_settings(self) -> dict:
        return self.storage.load_config()

    def update_settings(self, update_dict: dict[str, Any]) -> dict:
        """部分更新:None 值过滤(= 未传),其余落盘;返回更新后的完整 config。"""
        config = self.storage.load_config()
        config.update({k: v for k, v in update_dict.items() if v is not None})
        self.storage.save_config(config)
        return config

    # -------------------------------------------------- performance profile
    def resolved_profile(self, override: str | None = None) -> str:
        """读路径 profile 解析:请求 override > config 存值 > balanced。
        此前该回退在 read_orchestrator 手抄两份。"""
        if override:
            return override
        return self.storage.load_config().get(
            "performance_profile", DEFAULT_READ_PROFILE
        )

    def config_with_profile(self, override: str | None = None) -> dict:
        """load_config + 解析好的 performance_profile(读路径调用方的常用组合)。"""
        config = self.storage.load_config()
        config["performance_profile"] = override or config.get(
            "performance_profile", DEFAULT_READ_PROFILE
        )
        return config

    # -------------------------------------------------------------- /engines
    def get_engines(self) -> dict:
        """默认 + 存储 的合并结果:新 schema 字段始终出现,与引擎实际读取的
        load_engines() 合并逻辑一致。"""
        config = self.storage.load_config()
        stored = config.get("engines")
        merged = reader_bridge.default_engines()
        if isinstance(stored, dict):
            merged = reader_bridge.deep_merge_engines(merged, stored)
        return merged

    def update_engines(self, update: dict | None) -> None:
        config = self.storage.load_config()
        engines = config.get("engines")
        if not isinstance(engines, dict):
            engines = reader_bridge.default_engines()
        engines = reader_bridge.deep_merge_engines(engines, update or {})
        config["engines"] = engines
        self.storage.save_config(config)

    def store_engine_credential(
        self,
        family: str,
        provider: str,
        key: str | None,
        region: str | None = None,
    ) -> bool:
        """/engines/check 的「先持久化凭证再探测」写路径(不改 selected)。
        返回是否有写入。行为保持自原路由内联逻辑。"""
        if key is None:
            return False
        config = self.storage.load_config()
        engines = config.get("engines")
        if not isinstance(engines, dict):
            engines = reader_bridge.default_engines()

        modified = False
        if family == "llm":
            llm = engines.setdefault("llm", {})
            if provider == "local":
                llm["local_model_path"] = key
            else:
                llm.setdefault("keys", {})[provider] = key
            modified = True
        elif family == "translate":
            tr = engines.setdefault("translate", {})
            if provider == "microsoft":
                tr["microsoft_key"] = key
                if region:
                    tr["microsoft_region"] = region
            elif provider == "deepl":
                tr["deepl_key"] = key
            # 行为保持:family=translate 且带 key 即视为有修改(与原路由一致)
            modified = True

        if modified:
            config["engines"] = engines
            self.storage.save_config(config)
        return modified
