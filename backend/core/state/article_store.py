"""ArticleStore — 「当前文章」的唯一读写口(计划 #10 C3.3)。

此前 current_article 的读写散在 orchestrator(新读/RESTART)、
playback_service(play_wav_file、节流索引持久化)、/seek、/snapshot 五处,
各自 load_state→改 dict→save_state。收口后:state.json 是本模块身后的持久化
adapter,格式不变(其他 state 键原样保留);实时索引仍以 player 为权威,
/snapshot 用 view(live_index=…) 合成展示视图。

本模块无内部状态(纯粹包住 storage),多实例等价——seam 在模块而非单例。
"""
from __future__ import annotations

from typing import Any


class ArticleStore:
    def __init__(self, storage: Any) -> None:
        self.storage = storage

    def get(self) -> dict:
        """当前文章 dict(可能为 {});返回的是可变副本,改动需经 replace/set_index 落盘。"""
        return self.storage.load_state().get("current_article", {})

    def replace(self, article: dict) -> None:
        """整体替换当前文章(新朗读/RESTART/播客 WAV 场景)。保留 state 其它键。"""
        state = self.storage.load_state()
        state["current_article"] = article
        self.storage.save_state(state)

    def set_index(self, idx: int, *, expect_title: str | None = None) -> bool:
        """只更新播放索引。expect_title 非 None 时要求标题匹配才写
        (播放线程的防串写守卫,原 _persist_current_index 语义)。"""
        state = self.storage.load_state()
        article = state.get("current_article")
        if not article:
            return False
        if expect_title is not None and article.get("title") != expect_title:
            return False
        article["current_index"] = idx
        self.storage.save_state(state)
        return True

    def view(self, live_index: int | None = None) -> dict:
        """/snapshot 的文章展示视图(原逐请求对账块,C3.4 收编于此):
        chunks 清洗成纯文本;live_index 在范围内时覆盖持久化索引并给出进度串。"""
        article = self.get()
        chunks = article.get("chunks", [])
        chunks_clean = [c["text"] if isinstance(c, dict) else c for c in chunks]
        current_index = article.get("current_index", 0)
        progress_override: str | None = None
        if live_index is not None and live_index < len(chunks):
            current_index = live_index
            progress_override = f"{live_index + 1}/{len(chunks)}"
        return {
            "chunks_clean": chunks_clean,
            "current_index": current_index,
            "progress_override": progress_override,
        }
