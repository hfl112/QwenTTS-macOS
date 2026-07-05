"""条目展示标签的单一真相(计划 #11 N2)。

用户拍板的分类体系(2026-07-04):每个条目 = [信息源标签] [模式标签] 干净标题。
此前 App 与扩展各自从 title 前缀/文件名反推标签,词表已分歧(28 字 vs 40 字
截断、"双人讨论" vs "双人总结")。收口后:三个列表端点统一输出
display_title / source_label / mode_label,前端只渲染不加工,截断交给
CSS ellipsis / SwiftUI lineLimit。
"""
from __future__ import annotations

import re
import time

from core import reader_bridge

SOURCE_LABELS = {
    "clipboard": "剪贴板",
    "url": "网页",
    "web": "网页",
    "video": "视频",
    "selection": "选中",
    "cache": "缓存",
}

MODE_LABELS = {
    "original": "原文",
    "translate": "译文",
    "dual-summary": "双人总结",
    "dual-trans": "双人翻译",
}

# 历史遗留:reader_service 曾把模式烤进标题前缀;干净标题要剥掉它们。
_LEGACY_PREFIX_RE = re.compile(r"^(\[双人总结\]|\[双人翻译\]|\[翻译\]|\[译·[^\]]*\])\s*")


def source_label(source: str | None) -> str:
    s = (source or "").strip()
    return SOURCE_LABELS.get(s, s)


def mode_label(mode: str | None) -> str:
    m = reader_bridge.normalize_mode(mode)
    return MODE_LABELS.get(m, m)


_PREFIX_TO_MODE = {
    "[双人总结]": "dual-summary",
    "[双人翻译]": "dual-trans",
    "[翻译]": "translate",
}


def infer_mode_from_legacy_prefix(title: str | None) -> str | None:
    """旧数据兼容(#12-②):停烤前缀之前,URL→保存 的内容形态只烤在标题里。
    新数据走 content_mode 字段,本函数只服务历史条目。"""
    t = (title or "").strip()
    for prefix, mode in _PREFIX_TO_MODE.items():
        if t.startswith(prefix):
            return mode
    if t.startswith("[译·"):
        return "translate"
    return None


def clean_display_title(title: str | None, fallback_text: str = "", max_len: int = 80) -> str:
    """干净标题:剥历史模式前缀;空则取正文首行(语义截断,视觉截断归前端)。"""
    t = _LEGACY_PREFIX_RE.sub("", (title or "").strip())
    if t:
        return t
    first_line = (fallback_text or "").strip().split("\n", 1)[0].strip()
    return first_line[:max_len]


# --- 进行中 URL 抓取的伪行规则(M4-③,计划 #13) ---------------------------------
# /saved_items 与播客 list_files 都要给「正在抓取」的 URL 插一条占位行;
# 过滤规则(60s 窗口 + is_podcast 分流)与提示文案收在这一份,不再各抄。

PENDING_FETCH_TITLE = "⏳ 正在抓取网页正文..."
PENDING_FETCH_WINDOW_SEC = 60.0


def pending_url_tasks(
    active_url_tasks: dict, *, podcast: bool, now: float | None = None
) -> list[tuple[str, dict]]:
    """按类型过滤、仍在窗口内的进行中 URL 任务 [(url, info)…]。行的具体字段
    形状由各调用方决定(saved 行与播客文件行的 wire 形状本就不同)。"""
    now = time.time() if now is None else now
    return [
        (url, info)
        for url, info in list(active_url_tasks.items())
        if info.get("is_podcast", False) == podcast
        and now - info["timestamp"] < PENDING_FETCH_WINDOW_SEC
    ]
