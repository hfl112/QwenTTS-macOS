"""文字模式(mode)的官方词表与规范化(计划 #11 N1)。

2026-07-04 用户拍板改名:podcast-discuss → dual-summary、podcast-trans → dual-trans
(四种模式:original / translate / dual-summary / dual-trans)。
所有入口(App、扩展、CLI、旧数据)可能仍送旧值 —— 一律在边界经 normalize_mode
归一;旧成品的复用查重经 legacy_equivalents 做双 key 兼容,不作废已生成内容。
"""
from __future__ import annotations

CANONICAL_MODES = ("original", "translate", "dual-summary", "dual-trans")

# 旧值 → 规范值(只增不删:未来再改名往这里加一行)
LEGACY_MODE_ALIASES = {
    "podcast-discuss": "dual-summary",
    "podcast-trans": "dual-trans",
}


def normalize_mode(mode: str | None) -> str:
    """任意来路的 mode 字符串 → 规范值。空/None → original;未知值原样透传
    (上层各自决定怎么处理未知模式,与历史行为一致)。"""
    m = (mode or "original").strip() or "original"
    return LEGACY_MODE_ALIASES.get(m, m)


def legacy_equivalents(mode: str) -> list[str]:
    """规范值 → 历史等价旧值列表(复用查重/缓存回退用)。"""
    canonical = normalize_mode(mode)
    return [old for old, new in LEGACY_MODE_ALIASES.items() if new == canonical]
